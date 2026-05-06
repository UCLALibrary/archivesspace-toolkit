import argparse
import asnake.logging as logging
import copy
import re

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from asnake.client import ASnakeClient

from aspace_utils import (
    _get_container_refs_from_db,
    _get_ao_refs_for_top_container_from_db,
)


# Logger available globally within this module.
# Configuration is done by _configure_logging(), which is called by main().
# Made available globally so that tests can use the same logger with their own configuration.
logger = logging.get_logger(Path(__file__).stem)


def _configure_logging() -> None:
    """Configure logging for the application."""
    name = Path(__file__).stem
    logs_dir = Path("logs")  # save logs to "./logs/"
    logs_dir.mkdir(parents=True, exist_ok=True)  # create dir if it doesn't exist
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logging_filename_base = f"{name}_{timestamp}"
    logging_filename = logs_dir / f"{logging_filename_base}.log"
    logging.setup_logging(filename=logging_filename, level="INFO")


def _get_args() -> argparse.Namespace:
    """Get command-line arguments for this program."""
    parser = argparse.ArgumentParser(
        description="Cleanup compound indicators in ArchivesSpace."
    )
    parser.add_argument(
        "-c",
        "--config_file",
        type=str,
        required=True,
        help=(
            "Path to YAML config file with ArchivesSpace credentials, "
            "including database connection settings."
        ),
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
        "--dry_run",
        action="store_true",
        help="Log intended actions without updating ArchivesSpace.",
    )
    return parser.parse_args()


def _expand_range(indicator: str) -> list[str]:
    """Expand a numeric range into individual values, if possible.
        E.g. "38-42" -> ["38", "39", "40", "41", "42"].

    :param str indicator: A single indicator segment to expand.
    :return: A list of individual indicator values.
    :raises ValueError: If the range cannot be expanded and requires manual review.
    """
    left, right = indicator.split("-")
    if left.isdigit() and right.isdigit():
        start, end = int(left), int(right)
        if start > end:
            # Start of range cannot be greater than end of range.
            raise ValueError(f"Start {start} > end {end}")
        return [f"{n}" for n in range(int(left), int(right) + 1)]
    # Anything else is not a valid numeric range.
    raise ValueError(f"Not a valid numeric range")


def _parse_compound_indicator(indicator: str) -> list[str]:
    """Parse a compound indicator into its individual values.

    For example:
    - Comma-separated lists: "29, 32a, 37" -> ["29", "32a", "37"]
    - Numeric ranges: "38-42" -> ["38", "39", "40", "41", "42"]
    - Mixed: "1-3, 5a" -> ["1", "2", "3", "5a"]

    :param str indicator: The compound indicator to parse.
    :return: A flat list of individual indicator values.
    """
    # Split on commas first to handle mixed cases, then expand any ranges in each part.
    parts = [
        part.strip() for part in indicator.split(",") if part.strip()
    ]  # `if part.strip()` filters out any empty segments

    expanded: list[str] = []
    for part in parts:
        if re.match(r"^\[?\d+-\d+\]?$", part):  # match 1-3 or [1-3]
            # Remove any brackets from range.
            part = part.strip("[]")
            try:
                expanded_range = _expand_range(part)
                expanded.extend(expanded_range)
            except ValueError as err:
                # Re-raise a ValueError if a range part cannot be expanded,
                # so caller can log a warning and skip this indicator.
                raise ValueError(f"Cannot expand range '{part}': {err}")
        # Individual indicators must be alphanumeric, like 1, 2, or 3a
        elif part.isalnum():
            expanded.append(part)
        # Everything else cannot be parsed safely and requires manual review.
        else:
            raise ValueError(f"Unexpected indicator format: '{indicator}'")
    # Return a deduplicated list of individual indicators, preserving order.
    return list(dict.fromkeys(expanded))


def _get_all_top_containers(
    aspace_client: ASnakeClient,
    resource_uri: str,
    resource_id: int,
    db_config: dict,
) -> tuple[list[dict], set[str], dict[str, list[dict]]]:
    """Fetch all top containers linked to the given resource.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str resource_uri: The URI of the resource to process.
    :param int resource_id: The ID of the resource to process.
    :param dict db_config: DB connection settings.
    :return: A tuple of:
        - all_tcs: full dictionaries for every top container linked to the resource.
        - existing_indicators: set of all existing indicator strings for the resource.
            Used for quick checks if an indicator already exists.
        - tcs_by_indicator: dict with indicators as keys,
            and lists of TCs with that indicator as values, for duplicate checks.
    """
    container_refs = _get_container_refs_from_db(db_config, resource_id)

    logger.info(
        f"Fetched {len(container_refs)} container ref{'s' if len(container_refs) > 1 else ''} "
        f"from {resource_uri}"
    )

    all_tcs: list[dict] = []
    for ref in container_refs:
        try:
            tc = aspace_client.get(ref).json()
        except Exception as err:
            logger.error(f"Error fetching top container {ref}: {err}. Skipping.")
            continue
        all_tcs.append(tc)

    existing_indicators: set[str] = {tc.get("indicator", "") for tc in all_tcs}

    # Group top containers by indicator to surface duplicates and make lookups quicker.
    tcs_by_indicator: defaultdict[str, list[dict]] = defaultdict(list)
    for tc in all_tcs:
        indicator = tc.get("indicator", "")
        tcs_by_indicator[indicator].append(tc)

    return all_tcs, existing_indicators, tcs_by_indicator


def _build_new_top_container(compound_tc: dict, new_indicator: str) -> dict:
    """Build a new top container using `new_indicator`,
    while copying all other fields from `compound_tc`.

    :param dict compound_tc: The source top container record with a compound indicator.
    :param str new_indicator: The indicator value for the new top container.
    :return: The new top container record.
    """
    new_tc = copy.deepcopy(compound_tc)
    new_tc["indicator"] = new_indicator
    return new_tc


def _post_top_container(
    aspace_client: ASnakeClient, repo_id: int, tc_body: dict, dry_run: bool
) -> str | None:
    """POST a new top container to ArchivesSpace.

    Returns the new TC URI on success, or None if dry_run is True or the POST fails.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param int repo_id: ASpace repository ID.
    :param dict tc_body: JSONModel body for the new top container.
    :param bool dry_run: If True, log the intended action without making the API call.
    """
    if not dry_run:
        try:
            response = aspace_client.post(
                f"/repositories/{repo_id}/top_containers", json=tc_body
            ).json()
            new_uri = f"/repositories/{repo_id}/top_containers/{response['id']}"
        except Exception as err:
            logger.error(f"Error posting top container: {err}. Skipping.")
            return None
    else:
        new_uri = f"/repositories/{repo_id}/top_containers/{{NEW_TC_ID}}"
    logger.info(
        f"{'DRY RUN: Would create' if dry_run else 'Created'} new top container {new_uri}"
    )
    return new_uri


def _post_archival_object(
    aspace_client: ASnakeClient,
    ao_ref: str,
    ao_body: dict,
    dry_run: bool,
) -> None:
    """POST an archival object to ArchivesSpace.

    NOTE: The ASpace API uses POST to update archival objects,
    not PUT or PATCH.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str ao_ref: The URI of the archival object to update.
    :param dict ao_body: The body of the archival object.
    :param bool dry_run: If True, log the intended updates without POSTing.
    """
    if not dry_run:
        try:
            aspace_client.post(ao_ref, json=ao_body).json()
        except Exception as err:
            logger.error(f"Error posting archival object: {err}. Skipping.")
            return None
    logger.info(
        f"{'DRY RUN: Would update' if dry_run else 'Updated'} "
        f"instance(s) on archival object {ao_ref}"
    )


def _relink_archival_objects(
    aspace_client: ASnakeClient,
    original_tc: dict,
    new_tc_uris: list[str],
    db_config: dict,
    dry_run: bool,
) -> None:
    """Handles relinking archival objects from `original_tc`
    to individual top containers `new_tc_uris`.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict original_tc: The compound top container record being replaced.
    :param list[str] new_tc_uris: URIs of individual top containers to link.
    :param dict db_config: DB connection settings.
    :param bool dry_run: If True, log the intended updates without POSTing.
    """
    # Parse ID from original TC URI,
    # because it's much faster when querying the DB.
    original_tc_uri = original_tc.get("uri", "")
    original_tc_id = int(original_tc_uri.split("/")[-1])

    # There is no good way to retrieve all AOs linked to a TC via the API,
    # so we use a database query instead.
    ao_refs = _get_ao_refs_for_top_container_from_db(db_config, original_tc_id)
    logger.info(
        f"Found {len(ao_refs)} archival object{'s' if len(ao_refs) > 1 else ''} "
        f"linked to compound top container {original_tc_uri}"
    )

    # For each AO linked to the original TC,
    # relink it to each of the new, individual TCs.
    for ao_ref in ao_refs:
        try:
            archival_object = aspace_client.get(ao_ref).json()
        except Exception as err:
            logger.error(f"Error fetching archival object {ao_ref}: {err}. Skipping.")
            continue

        # Find the instance within the AO with ref to the original TC.
        # The path of the ref is instance > sub_container > top_container > ref.
        original_instances = archival_object.get("instances", [])
        new_instances = []
        instance_type = ""
        for instance in original_instances:
            sub_container = instance.get("sub_container", {})
            top_container = sub_container.get("top_container", {})
            if top_container.get("ref") == original_tc_uri:
                # Preserve instance type from original instance.
                instance_type = instance.get("instance_type", "")

        # Add new instances to the AO with ref to each of the new, individual TCs,
        # with instance type preserved from original instance.
        for new_tc_uri in new_tc_uris:
            new_instances.append(
                {
                    "instance_type": instance_type,
                    "sub_container": {"top_container": {"ref": new_tc_uri}},
                }
            )
            logger.info(
                f"Relinked archival object {ao_ref} to top container {new_tc_uri}"
            )

        # If the instances have changed, POST the updated AO to ArchivesSpace,
        # or log the intended update if dry_run.
        if original_instances != new_instances:
            archival_object["instances"] = new_instances
            _post_archival_object(aspace_client, ao_ref, archival_object, dry_run)


def _delete_top_container(
    aspace_client: ASnakeClient, tc_uri: str, dry_run: bool
) -> None:
    """Delete the given top container URI.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str tc_uri: The URI of the top container to delete.
    :param bool dry_run: If True, log the intended deletion without making the API call.
    """
    if not dry_run:
        try:
            aspace_client.delete(tc_uri).json()
        except Exception as err:
            logger.error(f"Error deleting top container {tc_uri}: {err}. Skipping.")
            return None
    logger.info(
        f"{'DRY RUN: Would delete' if dry_run else 'Deleted'} "
        f"compound top container {tc_uri}"
    )


def _process_resource(
    aspace_client: ASnakeClient,
    repo_id: int,
    resource_id: int,
    db_config: dict,
    dry_run: bool,
) -> None:
    """Cleanup compound box indicators for the given resource.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param int repo_id: Target ASpace repository ID.
    :param int resource_id: ASpace resource ID for the target collection.
    :param dict db_config: DB connection settings.
    :param bool dry_run: If True, log all intended changes without making API writes.
    """
    if dry_run:
        logger.info(f"DRY RUN--NO UPDATES WILL BE MADE")

    resource_uri = f"/repositories/{repo_id}/resources/{resource_id}"
    logger.info(f"Processing resource at {resource_uri}")

    all_tcs, existing_indicators, tcs_by_indicator = _get_all_top_containers(
        aspace_client, resource_uri, resource_id, db_config
    )

    compound_tcs = [
        tc
        for tc in all_tcs
        if tc.get("type", "") == "box"
        and any(
            delimiter in tc.get("indicator", "") for delimiter in [",", "-"]
        )  # check for comma or hyphen in indicator
    ]
    logger.info(
        f"Found {len(compound_tcs)} top container{'s' if len(compound_tcs) > 1 else ''} "
        f"with compound indicators at {resource_uri}"
    )

    for compound_tc in compound_tcs:
        compound_uri = compound_tc.get("uri", "")
        compound_indicator = compound_tc.get("indicator", "")

        try:
            individual_indicators = _parse_compound_indicator(compound_indicator)
        except ValueError as err:
            # Skip indicators that cannot be safely parsed.
            logger.warning(
                f"Cannot safely parse compound indicator on {compound_uri}: {err}. "
                f"Manual review required."
            )
            continue

        logger.info(
            f"Parsed compound indicator for {compound_uri}: "
            f"'{compound_indicator}' -> {individual_indicators}"
        )

        # List to hold URIs for the individual top containers
        # that will be used for archival object relinking later.
        individual_uris: list[str] = []
        for indicator in individual_indicators:
            # Check if the indicator already exists in the collection,
            # and if so, add it to the list of individual URIs.
            if indicator in existing_indicators:
                existing_tcs = tcs_by_indicator[indicator]
                # Log any cases where there is more than 1 TC with the same indicator
                if len(existing_tcs) > 1:
                    logger.warning(
                        f"Found more than one top container with indicator '{indicator}' "
                        f"in resource {resource_id}: {[tc.get('uri') for tc in existing_tcs]}. "
                        "Manual review required."
                    )
                    continue
                # After duplicate check, there should only be one TC per indicator,
                # so we can safely use the first one.
                existing_uri = existing_tcs[0].get("uri", "")
                individual_uris.append(existing_uri)
                logger.info(
                    f"Top container with indicator '{indicator}' already exists at "
                    f"{existing_uri}. It will be reused."
                )
            # Otherwise, build a new top container for the indicator,
            # with the same type and container profile as the compound TC,
            # then post it to ArchivesSpace.
            else:
                new_body = _build_new_top_container(compound_tc, indicator)
                new_uri = _post_top_container(aspace_client, repo_id, new_body, dry_run)
                if new_uri:
                    individual_uris.append(new_uri)
                    existing_indicators.add(indicator)
                    tcs_by_indicator[indicator].append(
                        {"uri": new_uri, "indicator": indicator}
                    )
                else:
                    logger.error(
                        f"Failed to create top container for indicator '{indicator}': {new_body}"
                    )
                    continue
        # Use list of reused or new top containers to relink archival objects
        # then delete the original compound top container.
        _relink_archival_objects(
            aspace_client, compound_tc, individual_uris, db_config, dry_run
        )
        _delete_top_container(aspace_client, compound_uri, dry_run)


def main() -> None:
    """Cleanup top containers in ArchivesSpace that have a compound box indicator,
    meaning the indicator is given as either a comma-separated list or a range.

    Summary of steps provided by LSC:
        1. Get all top containers for a given collection.
        2. Find containers with compound indicators.
        3. Split each compound indicator into individual values.
        4. For each value, reuse a matching existing container if found,
            or create a new one with copied metadata.
        5. For each archival object linked to the compound container,
            add new instances linked to each single-box container.
        6. Remove archival object links to the original compound container.
        7. Delete the original compound container.
    """
    _configure_logging()
    args = _get_args()

    aspace_client = ASnakeClient(config_file=args.config_file)
    db_config = aspace_client.config.get("database")
    if not db_config:
        raise ValueError("DB connection settings are required.")

    _process_resource(
        aspace_client=aspace_client,
        repo_id=args.repo_id,
        resource_id=args.resource_id,
        db_config=db_config,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
