import argparse
from asnake.client import ASnakeClient
from asnake.logging import get_logger
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
logger = get_logger(Path(__file__).stem)


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
        "-r",
        "--resource_id",
        type=int,
        required=True,
        help="ArchivesSpace resource ID to process.",
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
            tc = aspace_client.get(ref).json()
        except Exception as err:
            logger.error(f"Error fetching top container {ref}: {err}. Skipping.")
            continue
        type = tc.get("type", "")
        indicator = tc.get("indicator", "")
        tcs_by_indicator[(type, indicator)].append(tc)
    return tcs_by_indicator


def _count_ao_refs_for_tc(tc: dict, db_config: dict) -> int:
    """Count the number of archival object references for a top container.

    :param dict tc: Top container record.
    :param dict db_config: DB connection settings.
    :return: The number of archival object references for the top container.
    """
    tc_id = int(tc.get("uri", "0").split("/")[-1])
    return len(get_ao_refs_for_top_container_from_db(db_config, tc_id))


def _determine_canonical_tc(tcs: list[dict]) -> tuple[dict, list[dict]]:
    """Determine the canonical top container from a list of top container records.

    :param list[dict] tcs: List of top container records.
    :return: A tuple of the canonical top container and the remaining duplicate TCs.
    """

    canonical_tc = max(tcs, key=_count_ao_refs_for_tc)


def _merge_duplicate_containers(
    aspace_client: ASnakeClient,
    db_config: dict,
    resource_id: int,
) -> None:
    """Merge duplicate top containers in ArchivesSpace."""
    tcs_by_indicator = _get_tcs_by_indicator(aspace_client, db_config, resource_id)
    # Find TC records that have duplicate (type, indicator) keys
    duplicate_tcs: list[tuple[str, str, list[dict]]] = [
        (type, indicator, tcs)
        for (type, indicator), tcs in tcs_by_indicator.items()
        if len(tcs) > 1
    ]
    if not duplicate_tcs:
        logger.info("No duplicate top containers found for Resource ID {resource_id}.")
        return

    for type, indicator, tcs in duplicate_tcs:
        logger.info(
            f"Found {len(tcs)} top containers "
            f"with type '{type}' and indicator '{indicator}'"
        )

        # Add temporary field to each TC to store list of archival objects refs
        for tc in tcs:
            tc_id = int(tc.get("uri", "0").split("/")[-1])
            tc["ao_refs"] = get_ao_refs_for_top_container_from_db(db_config, tc_id)


def main() -> None:
    """Merge duplicate top containers in ArchivesSpace,
    for a given resource (i.e. collection).

    Summary of LSC ticket:
    1. Retrieve all top containers in the collection.
    2. Identify duplicate groups by type and indicator.
    3. For each group, designate a canonical top container,
      based on criteria provided by LSC.
    4. Relink archival objects to the canonical top container.
    5. Delete the duplicate top container(s).
    """
    configure_logging(Path(__file__).stem)
    args = _get_args()
    config = load_config(args.config_file)
    db_config = config.get("database")
    if not db_config:
        raise ValueError("DB connection settings are required.")
    aspace_client = ASnakeClient(**config)

    _merge_duplicate_containers(aspace_client, db_config, args.resource_id)


if __name__ == "__main__":
    main()
