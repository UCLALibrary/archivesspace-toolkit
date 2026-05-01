import argparse
import asnake.logging as logging
import copy
import re

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from asnake.client import ASnakeClient

from add_alma_barcodes_to_archivesspace import (
    _get_container_refs_from_api,
    _get_container_refs_from_db,
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
        description="Cleanup compound box indicators in ArchivesSpace."
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
        "--use_db",
        action="store_true",
        help="Get containers from database instead of API.",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Dry run: log would-be actions without updating ArchivesSpace.",
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
    resource_id: int,
    use_db: bool,
    db_config: dict | None,
) -> tuple[list[dict], set[str], dict[str, list[dict]]]:
    """Fetch all top containers linked to the given resource.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param int resource_id: ASpace resource ID for the target collection.
    :param bool use_db: If True, fetch container refs via DB query; otherwise via API.
    :param dict db_config: DB connection settings, required when use_db is True.
    :return: A tuple of:
        - all_tcs: full dictionaries for every top container linked to the resource.
        - existing_indicators: set of all existing indicator strings for the resource.
            Used for quick checks if an indicator already exists.
        - tcs_by_indicator: dict with indicators as keys,
            and lists of TCs with that indicator as values, for duplicate checks.
    """
    if use_db:
        if not db_config:
            raise ValueError("db_config is required when use_db is True")
        container_refs = _get_container_refs_from_db(db_config, resource_id)
    else:
        container_refs = _get_container_refs_from_api(aspace_client, resource_id)

    logger.info(
        f"Fetched {len(container_refs)} container refs for resource {resource_id}"
    )

    all_tcs: list[dict] = []
    for ref in container_refs:
        tc = aspace_client.get(ref).json()
        if "error" in tc:
            logger.error(f"Error fetching top container {ref}: {tc['error']}")
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
    if dry_run:
        logger.info(
            f"DRY RUN: Would create top container for "
            f"{tc_body.get('type')} {tc_body.get('indicator')}"
        )
        return f"MOCK URI FOR {tc_body.get('type')} {tc_body.get('indicator')}"

    response = aspace_client.post(
        f"/repositories/{repo_id}/top_containers", json=tc_body
    ).json()
    if response.get("status") != "Created":
        logger.error(f"Failed to create top container {tc_body}: {response}")
        return None

    new_uri = f"/repositories/{repo_id}/top_containers/{response['id']}"
    logger.info(f"Created top container {new_uri}")
    return new_uri


def _relink_and_cleanup_archival_objects(
    aspace_client: ASnakeClient,
    compound_tc: dict,
    new_tc_uris: list[str],
    dry_run: bool,
) -> None:
    """Handles relinking archival objects from `compound_tc`
    to individual top containers (`new_tc_uris`).

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict compound_tc: The compound top container record being replaced.
    :param list[str] new_tc_uris: URIs of individual top containers to link.
    :param bool dry_run: If True, log the intended updates without POSTing.
    """
    # Get archival object instances linked to the compound top container.
    compound_uri = compound_tc.get("uri")
    # TODO: Figure out how to relink archival objects from the original compound container
    # to the new individual containers.
    pass


def _delete_top_container(
    aspace_client: ASnakeClient, tc_uri: str, dry_run: bool
) -> None:
    """Delete the given top container URI."""
    if dry_run:
        logger.info(f"DRY RUN: Would delete compound top container {tc_uri}")
        return

    response = aspace_client.delete(tc_uri).json()
    if response.get("status") != "Deleted":
        logger.error(f"Failed to delete top container {tc_uri}: {response}")
        return
    logger.info(f"Deleted compound top container {tc_uri}")


def _process_resource(
    aspace_client: ASnakeClient,
    repo_id: int,
    resource_id: int,
    use_db: bool,
    db_config: dict | None,
    dry_run: bool,
) -> None:
    """Cleanup compound box indicators for the given resource.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param int repo_id: Target ASpace repository ID.
    :param int resource_id: ASpace resource ID for the target collection.
    :param bool use_db: If True, fetch container refs via DB query, otherwise via API,
        which may timeout if the resource has many top containers.
    :param dict db_config: DB connection settings, required when use_db is True.
    :param bool dry_run: If True, log all intended changes without making API writes.
    """
    if dry_run:
        logger.info(f"DRY RUN--NO UPDATES WILL BE MADE")
    logger.info(f"Processing resource {resource_id}")

    all_tcs, existing_indicators, tcs_by_indicator = _get_all_top_containers(
        aspace_client, resource_id, use_db, db_config
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
        f"Found {len(compound_tcs)} top containers "
        f"with compound indicators in resource {resource_id}"
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
        _relink_and_cleanup_archival_objects(
            aspace_client, compound_tc, individual_uris, dry_run
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
    db_config = aspace_client.config.get("database") if args.use_db else None

    _process_resource(
        aspace_client=aspace_client,
        repo_id=args.repo_id,
        resource_id=args.resource_id,
        use_db=args.use_db,
        db_config=db_config,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
