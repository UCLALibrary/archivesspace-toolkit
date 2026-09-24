import argparse

from asnake import logging
from asnake.client import ASnakeClient
from asnake.jsonmodel import JM
from collections import defaultdict
from pathlib import Path

from utils import configure_logging, load_config
from utils.aspace_utils import (
    find_false_duplicates,
    get_container_refs_from_db,
    get_ao_refs_for_top_container_from_db,
    get_required_db_settings,
)

# Logger available globally within this module.
# Configuration is done by configure_logging(), which is called by main().
# Made available globally so that tests can use the same logger with their own configuration.
logger = logging.get_logger(Path(__file__).stem)


def _get_args() -> argparse.Namespace:
    """Get command-line arguments for this program."""
    parser = argparse.ArgumentParser(
        description="Merge duplicate top containers in ArchivesSpace."
    )
    parser.add_argument(
        "-c",
        "--config_file",
        type=str,
        required=True,
        help="Path to YAML config file with ArchivesSpace credentials.",
    )
    parser.add_argument(
        "--repo_id",
        type=int,
        required=False,
        default=2,
        help="ArchivesSpace repository ID to target. Defaults to 2.",
    )
    parser.add_argument(
        "-r",
        "--resource_id",
        type=int,
        required=True,
        help="ArchivesSpace resource ID to process.",
    )
    parser.add_argument(
        "-d",
        "--dry_run",
        action="store_true",
        help="Run in dry run mode, without making any changes to ArchivesSpace.",
    )
    return parser.parse_args()


def _get_tcs_grouped_by_type_and_indicator(
    aspace_client: ASnakeClient,
    db_config: dict,
    resource_id: int,
) -> defaultdict[tuple[str, str], list[dict]]:
    """Get all top containers in the resource grouped by (type, indicator).

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict db_config: DB connection settings.
    :param int resource_id: The ID of the resource to process.
    :return: A dictionary with (type, indicator) keys, and lists of top container records as values.
    """
    container_refs = get_container_refs_from_db(db_config, resource_id)
    logger.info(
        f"Fetched {len(container_refs)} container{'s' if len(container_refs) > 1 else ''} "
        f"for resource ID {resource_id}"
    )

    # Group top containers by (type, indicator) to identify duplicates
    tcs_grouped_by_type_and_indicator: defaultdict[tuple[str, str], list[dict]] = (
        defaultdict(list)
    )
    for ref in container_refs:
        try:
            response = aspace_client.get(ref)
            response.raise_for_status()
            tc = response.json()
        except Exception as err:
            logger.error(f"Error fetching top container {ref}: {err}. Skipping.")
            continue
        type = tc.get("type", "")
        indicator = tc.get("indicator", "")
        tcs_grouped_by_type_and_indicator[(type, indicator)].append(tc)
    return tcs_grouped_by_type_and_indicator


def _resolve_aos_for_tcs(
    aspace_client: ASnakeClient, db_config: dict, tcs: list[dict]
) -> list[dict]:
    """Resolve archival object refs to archival object dicts for a list of top container records.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict db_config: DB connection settings.
    :param list[dict] tcs: List of top container records.
    :return: A list of top container records
        with related archival object dicts stored in a temporary field.
    """
    for tc in tcs:
        tc["_related_aos_temp"] = []
        tc_id = int(tc.get("uri", "0").split("/")[-1])
        ao_refs = get_ao_refs_for_top_container_from_db(db_config, tc_id)
        for ao_ref in ao_refs:
            try:
                response = aspace_client.get(ao_ref)
                response.raise_for_status()
                archival_object = response.json()
                tc["_related_aos_temp"].append(archival_object)
            except Exception as err:
                logger.error(
                    f"Error fetching archival object {ao_ref}: {err}. Skipping."
                )
                continue
    return tcs


def _has_location_data(tcs: list[dict]) -> bool:
    """Check for location data on a list of top container records,
    logging a warning and returning True if found, False otherwise.

    :param list[dict] tcs: List of top container records.
    :return: True if any top container has location data, False otherwise.
    """
    for tc in tcs:
        locations = tc.get("container_locations", [])
        if locations:
            logger.warning(
                f"Top container {tc.get('uri')} has location data: "
                f"{[location.get('ref') for location in locations]}"
            )
            return True
    return False


def _partition_false_duplicates(
    tcs: list[dict], false_duplicates: dict[str, str]
) -> tuple[list[dict], list[dict]]:
    """Split a duplicate group into real containers and "false duplicates"
    (placeholders for backlog / accession material), logging each false duplicate.
    False duplicates must never be merged, in either direction.

    :param list[dict] tcs: List of top container records.
    :param dict[str, str] false_duplicates: Mapping of false duplicate URI to reason,
        from `find_false_duplicates`.
    :return: A tuple of (real top containers, false duplicate top containers).
    """
    real_tcs = []
    false_tcs = []
    for tc in tcs:
        reason = false_duplicates.get(tc.get("uri", ""))
        if reason:
            logger.warning(
                f"Excluding false duplicate top container {tc.get('uri')} "
                f"from merge: {reason}"
            )
            false_tcs.append(tc)
        else:
            real_tcs.append(tc)
    return real_tcs, false_tcs


def _determine_canonical_tc(tcs: list[dict]) -> tuple[dict, list[dict]]:
    """Determine the canonical top container from a list of top container records.

    Uses the record with the most related archival objects,
    or the oldest creation time if there are ties.

    :param list[dict] tcs: List of top container records.
    :return: A tuple of the canonical top container and the remaining duplicate TCs.
    """
    # Had help from LLM for this concise implementation.
    # Selects the minimum value of the tuple returned by the lambda function,
    # which is the TC with the most related archival objects (i.e. smallest negative value)
    # or the earliest (i.e. minimum) `create_time` if there are ties in the AO counts.
    canonical = min(
        tcs,
        key=lambda tc: (
            -len(tc.get("_related_aos_temp", [])),
            # If a TC is missing the `create_time` field,
            # default to a value that sorts after TCs with a create time
            tc.get("create_time", "9999-01-01T00:00:00Z"),
        ),
    )
    return canonical, [tc for tc in tcs if tc is not canonical]


def _merge_top_containers(
    aspace_client: ASnakeClient,
    canonical_tc: dict,
    duplicate_tcs: list[dict],
    repo_id: int,
    dry_run: bool,
) -> bool:
    """Merge duplicate top containers into the canonical top container
    using the `/merge_requests/top_container` endpoint of the ArchivesSpace API.

    NOTE: The ArchivesSpace API endpoint used in this function is sparsely documented here:
    @https://archivesspace.github.io/archivesspace/api/?python#carry-out-a-merge-request-against-top-container-records
    From manual testing, it appears to manage relinking of related archival objects
    aggregating them all to point to the canonical top container,
    then deleting the TCs identified in the request as duplicates (i.e. the `merge_candidates`).

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict canonical_tc: The canonical top container record.
    :param list[dict] duplicate_tcs: The duplicate top container records.
    :param int repo_id: The ArchivesSpace repository ID.
    :param bool dry_run: If True, log the intended action without making the API call.
    :return: True if the merge request is successful, False otherwise.
    """
    # The `JM` helper class provides an easy way
    # to construct json payload for the request.
    request_body = JM.merge_requests(
        uri="/merge_requests/top_container",
        merge_destination={"ref": canonical_tc.get("uri")},
        merge_candidates=[{"ref": tc.get("uri")} for tc in duplicate_tcs],
    )

    logger.info(
        f"{'DRY RUN: Would merge' if dry_run else 'Merging'} "
        f"duplicate top containers {[tc.get('uri') for tc in duplicate_tcs]} "
        f"into canonical top container '{canonical_tc.get('uri')}'"
    )

    if not dry_run:
        try:
            response = aspace_client.post(
                "/merge_requests/top_container",
                params={"repo_id": repo_id},
                json=request_body,
            )
            response.raise_for_status()
        except Exception as err:
            logger.error(f"Error merging duplicate top containers: {err}. Skipping.")
            return False
    return True


def _print_summary(summary: dict, dry_run: bool) -> None:
    """Add summary info to the log and print a friendly message to the console.

    :param dict summary: Dict containing summary info for the run.
    :param bool dry_run: If True, print a dry run report.
    """
    lines = [
        f"{'*' * 5} {'DRY RUN' if dry_run else ''} SUMMARY {'*' * 5}",
        f"Total duplicate groups: {summary['Total duplicate groups']}",
        f"Groups with location data: {summary['Groups with location data']}",
        (
            f"Groups with false duplicates excluded:"
            f" {summary['Groups with false duplicates excluded']}"
        ),
        (
            f"Groups not merged (fewer than 2 real containers):"
            f" {summary['Groups not merged (fewer than 2 real containers)']}"
        ),
    ]
    if dry_run:
        # No merge requests are sent in a dry run, so failures can't occur.
        lines.append(f"Groups that would be merged: {summary['Successful merges']}")
    else:
        lines.extend(
            [
                f"Successful merges: {summary['Successful merges']}",
                f"Failed merges: {summary['Failed merges']}",
            ]
        )
    # Containers come from get_container_refs_from_db(), which only returns containers
    # linked to a published, unsuppressed AO. Say so, since it explains why groups
    # visible in the staff UI or in a direct database query may not appear here.
    lines.append(
        "Note: only top containers linked to at least one published, "
        "unsuppressed archival object are included."
    )
    lines.append(f"{'*' * len(lines[0])}")
    for line in lines:
        print(line)
        logger.info(line)


def _get_duplicate_groups(
    aspace_client: ASnakeClient,
    db_config: dict,
    resource_id: int,
) -> list[tuple[str, str, list[dict]]]:
    """Retrieve top containers in the resource
    and identify duplicate groups as those with the same type and indicator.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict db_config: DB connection settings.
    :param int resource_id: The ID of the resource to process.
    :return: A list of tuples representing duplicate groups,
    comprising type, indicator, and a list of top container records.
    """
    tcs_grouped_by_type_and_indicator = _get_tcs_grouped_by_type_and_indicator(
        aspace_client, db_config, resource_id
    )

    duplicate_groups: list[tuple[str, str, list[dict]]] = [
        (type, indicator, tcs)
        for (type, indicator), tcs in tcs_grouped_by_type_and_indicator.items()
        if len(tcs) > 1
    ]
    # Now sort the duplicate groups,
    # alphabetically by type and numerically by indicator,
    # to make review of the logs easier.
    duplicate_groups = sorted(
        duplicate_groups,
        key=lambda group: (
            group[0],  # sort alphabetically by type
            (
                int(group[1]) if group[1].isdigit() else group[1]
            ),  # then numerically by indicator, if possible
        ),
    )
    return duplicate_groups


def _process_duplicates_in_collection(
    aspace_client: ASnakeClient,
    db_config: dict,
    repo_id: int,
    resource_id: int,
    dry_run: bool,
) -> None:
    """Merge duplicate top containers in ArchivesSpace,
    for a given collection (identified by `resource_id`).

    Summary of LSC ticket:
        1. Retrieve all top containers in the collection.
        2. Identify duplicate groups by type and indicator.
        3. For each group, designate a canonical top container,
        based on criteria provided by LSC.
        3a. Exclude "false duplicates" (backlog / accession placeholders)
        from each group; only merge if 2+ real containers remain.
        4. Merge the duplicate top containers into the canonical top container,
        preserving archival object links in the process.
        5. Delete the duplicate top container(s).
    """
    duplicate_groups = _get_duplicate_groups(aspace_client, db_config, resource_id)
    # If no duplicate groups are found, log a note and return
    if not duplicate_groups:
        logger.info(f"No duplicate top containers found for Resource ID {resource_id}.")
        return

    summary = {
        "Total duplicate groups": len(duplicate_groups),
        "Groups with location data": 0,
        "Groups with false duplicates excluded": 0,
        "Groups not merged (fewer than 2 real containers)": 0,
        "Successful merges": 0,
        "Failed merges": 0,
    }
    for type, indicator, tcs in duplicate_groups:
        logger.info(
            f"Found {len(tcs)} top containers "
            f"with type '{type}' and indicator '{indicator}'"
        )

        # Check for any location data in the duplicate group.
        # Per ticket, this should not stop processing (no `continue` statement)
        # but should be logged for visibility.
        if _has_location_data(tcs):
            summary["Groups with location data"] += 1

        # Remove "false duplicates" (backlog / accession placeholders) from the group,
        # so they are never merged, then merge whatever real duplicates remain.
        false_duplicates = find_false_duplicates(db_config, tcs)
        tcs, false_tcs = _partition_false_duplicates(tcs, false_duplicates)
        if false_tcs:
            summary["Groups with false duplicates excluded"] += 1
        if len(tcs) < 2:
            logger.info(
                f"Not merging type '{type}' indicator '{indicator}': "
                f"{len(tcs)} real container(s) after excluding false duplicates"
            )
            summary["Groups not merged (fewer than 2 real containers)"] += 1
            continue

        # Resolve AO refs to their full dictionaries,
        # used to choose the canonical top container.
        tcs = _resolve_aos_for_tcs(aspace_client, db_config, tcs)

        canonical_tc, duplicate_tcs = _determine_canonical_tc(tcs)

        logger.info(
            f"Identified canonical top container '{canonical_tc.get('uri')}' "
            f"and {len(duplicate_tcs)} duplicate top container(s): "
            f"{[tc.get('uri') for tc in duplicate_tcs]}"
        )

        success = _merge_top_containers(
            aspace_client, canonical_tc, duplicate_tcs, repo_id, dry_run
        )
        if not success:
            summary["Failed merges"] += 1
            continue
        summary["Successful merges"] += 1

    _print_summary(summary, dry_run)


def main() -> None:
    """Entry-point for this program."""
    args = _get_args()

    log_filename_stem = Path(__file__).stem
    log_filename = configure_logging(log_filename_stem, args.dry_run)
    print(f"Logging to {log_filename}...")

    config = load_config(args.config_file)
    db_config = get_required_db_settings(config)
    aspace_client = ASnakeClient(**config)

    _process_duplicates_in_collection(
        aspace_client, db_config, args.repo_id, args.resource_id, args.dry_run
    )


if __name__ == "__main__":
    main()
