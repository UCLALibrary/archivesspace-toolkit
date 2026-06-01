import argparse

from asnake import logging
from asnake.client import ASnakeClient
from asnake.jsonmodel import JM
from collections import defaultdict
from pathlib import Path

from utils import configure_logging, load_config
from utils.aspace_utils import (
    get_container_refs_from_db,
    get_ao_refs_for_top_container_from_db,
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


def _get_tcs_by_indicator(
    aspace_client: ASnakeClient,
    db_config: dict,
    resource_id: int,
) -> defaultdict[tuple[str, str], list[dict]]:
    """Get all top containers in the collection."""
    container_refs = get_container_refs_from_db(db_config, resource_id)
    logger.info(
        f"Fetched {len(container_refs)} container{'s' if len(container_refs) > 1 else ''} "
        f"for resource ID {resource_id}"
    )

    # Group top containers by (type, indicator) to identify duplicates
    tcs_by_indicator: defaultdict[tuple[str, str], list[dict]] = defaultdict(list)
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
        tcs_by_indicator[(type, indicator)].append(tc)
    return tcs_by_indicator


def _resolve_ao_refs_for_tcs(
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


def _check_for_location_data(tcs: list[dict]) -> None:
    """Check for location data on a list of top container records
    representing a duplicate group, flagging the group for review if found.

    :param list[dict] tcs: List of top container records representing a duplicate group.
    """
    for tc in tcs:
        locations = tc.get("container_locations", [])
        if locations:
            logger.warning(
                f"Top container {tc.get('uri')} has location data: {locations}"
            )


def _has_recent_accession_keywords(tcs: list[dict]) -> bool:
    """Within a top container duplicate group, check for recent accession keywords
    in the titles of related archival objects,
    logging a warning and returning True if found, False otherwise.

    :param list[dict] tcs: List of top container records representing a duplicate group.
    :return: True if any recent accession keywords are found, False otherwise.
    """
    recent_accession_keywords = ["accession", "backlog"]
    for tc in tcs:
        for ao in tc["_related_aos_temp"]:
            if any(
                keyword in ao["title"].lower() for keyword in recent_accession_keywords
            ):
                logger.warning(
                    f"Archival object '{ao['uri']}' linked to top container '{tc['uri']}' "
                    f"may be recent accession: "
                    f"title='{ao['title']}'. Manual review of duplicate group required."
                )
                return True
    return False


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
    # or the oldest `create_time` if there are ties in the AO counts.
    canonical = min(
        tcs,
        key=lambda tc: (
            -len(tc["_related_aos_temp"]),
            # If a TC is missing the `create_time` field,
            # default to a future date so it sorts after TCs with a create time
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
        merge_destination={"ref": canonical_tc["uri"]},
        merge_candidates=[{"ref": tc["uri"]} for tc in duplicate_tcs],
    )

    logger.info(
        f"{'DRY RUN: Would merge' if dry_run else 'Merging'} "
        f"duplicate top containers {[tc['uri'] for tc in duplicate_tcs]} "
        f"into canonical top container '{canonical_tc['uri']}'"
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
        4. Merge the duplicate top containers into the canonical top container,
        preserving archival object links in the process.
        5. Delete the duplicate top container(s).
    """
    tcs_by_indicator = _get_tcs_by_indicator(aspace_client, db_config, resource_id)
    # Find TC records that have duplicate (type, indicator) keys
    duplicate_groups: list[tuple[str, str, list[dict]]] = [
        (type, indicator, tcs)
        for (type, indicator), tcs in tcs_by_indicator.items()
        if len(tcs) > 1
    ]
    if not duplicate_groups:
        logger.info(f"No duplicate top containers found for Resource ID {resource_id}.")
        return

    summary = {
        "Total duplicate groups": len(duplicate_groups),
        "Successful merges": 0,
        "Failed merges": 0,
        "Groups requiring manual review": 0,
    }
    for type, indicator, tcs in duplicate_groups:
        logger.info(
            f"Found {len(tcs)} top containers "
            f"with type '{type}' and indicator '{indicator}'"
        )

        # Check for any location data in the duplicate group.
        # Per ticket, this should not stop processing,
        # but should be logged for visibility in dry run mode.
        if dry_run:
            _check_for_location_data(tcs)

        # Resolve AO refs to their full dictionaries
        # to make it easier to check AO titles for recent accession keywords
        # on the whole top container group at once.
        tcs = _resolve_ao_refs_for_tcs(aspace_client, db_config, tcs)
        # Check for recent accession keywords in the titles of related archival objects
        # in the duplicate group, and stop processing the group if any are found.
        if _has_recent_accession_keywords(tcs):
            summary["Groups requiring manual review"] += 1
            continue

        canonical_tc, duplicate_tcs = _determine_canonical_tc(tcs)

        logger.info(
            f"Identified canonical top container '{canonical_tc['uri']}' "
            f"and {len(duplicate_tcs)} duplicate top container(s): "
            f"{[tc['uri'] for tc in duplicate_tcs]}"
        )

        success = _merge_top_containers(
            aspace_client, canonical_tc, duplicate_tcs, repo_id, dry_run
        )
        if not success:
            summary["Failed merges"] += 1
            continue
        summary["Successful merges"] += 1

    logger.info("Finished merging duplicate top containers")
    for key, value in summary.items():
        logger.info(f"{key}: {value}")


def main() -> None:
    """Entry-point for this program."""
    args = _get_args()
    configure_logging(Path(__file__).stem, args.dry_run)
    config = load_config(args.config_file)
    db_config = config.get("database")
    if not db_config:
        raise ValueError("DB connection settings are required.")
    aspace_client = ASnakeClient(**config)

    _process_duplicates_in_collection(
        aspace_client, db_config, args.repo_id, args.resource_id, args.dry_run
    )


if __name__ == "__main__":
    main()
