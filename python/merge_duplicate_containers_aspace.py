import argparse

from asnake import logging
from asnake.client import ASnakeClient
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


def _relink_archival_object_instances(
    archival_object: dict,
    source_tc_uri: str,
    target_tc_uri: str,
) -> dict:
    """Relink archival object instances from source to target top container.

    :param dict archival_object: The archival object dict to update.
    :param str source_tc_uri: The URI of the source top container.
    :param str target_tc_uri: The URI of the target top container.
    :return: The updated archival object dict.
    """
    for instance in archival_object.get("instances", []):
        sub_container = instance.get("sub_container", {})
        top_container = sub_container.get("top_container", {})
        if top_container.get("ref", "") == source_tc_uri:
            instance["sub_container"]["top_container"]["ref"] = target_tc_uri
    return archival_object


def _handle_duplicate_top_containers(
    aspace_client: ASnakeClient,
    canonical_tc: dict,
    duplicate_tcs: list[dict],
    dry_run: bool,
) -> None:
    """Handle duplicate top containers by relinking related archival objects
    to the canonical top container, then deleting the duplicate top containers.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict canonical_tc: The canonical top container dict.
    :param list[dict] duplicate_tcs: The duplicate top container dicts.
    :param bool dry_run: If True, log the intended updates without updating.
    """
    for duplicate_tc in duplicate_tcs:
        logger.info(f"Processing duplicate top container '{duplicate_tc['uri']}'...")
        logger.info(
            f"Found {len(duplicate_tc['_related_aos_temp'])} linked archival object(s)..."
        )

        # Relink each archival object from duplicate to canonical top container
        for original_ao in duplicate_tc["_related_aos_temp"]:
            ao_ref = original_ao["uri"]
            logger.info(
                f"{'DRY RUN: Would relink' if dry_run else 'Relinking'} "
                f"archival object '{original_ao['uri']}' "
                f"from '{duplicate_tc['uri']}' to '{canonical_tc['uri']}'"
            )

            updated_ao = _relink_archival_object_instances(
                original_ao, duplicate_tc["uri"], canonical_tc["uri"]
            )

            if original_ao != updated_ao:
                if dry_run:
                    logger.info(
                        f"DRY RUN: Would apply updates " f"to instance(s) on {ao_ref}"
                    )
                else:
                    try:
                        response = aspace_client.post(ao_ref, json=updated_ao)
                        response.raise_for_status()
                    except Exception as err:
                        logger.error(
                            f"Error updating archival object {ao_ref}: {err}. Skipping."
                        )
                        continue
                    logger.info(f"Applied updates to instance(s) on {ao_ref}")

        # Delete duplicate top container after relinking archival objects
        if dry_run:
            logger.info(
                f"DRY RUN: Would delete duplicate top container '{duplicate_tc['uri']}'"
            )
        else:
            try:
                response = aspace_client.delete(duplicate_tc["uri"])
                response.raise_for_status()
            except Exception as err:
                logger.error(
                    f"Error deleting duplicate top container "
                    f"{duplicate_tc['uri']}: {err}. Skipping."
                )
                continue
            logger.info(f"Deleted duplicate top container '{duplicate_tc['uri']}'")


def _merge_duplicate_containers(
    aspace_client: ASnakeClient,
    db_config: dict,
    resource_id: int,
    dry_run: bool,
) -> None:
    """Merge duplicate top containers in ArchivesSpace."""
    tcs_by_indicator = _get_tcs_by_indicator(aspace_client, db_config, resource_id)
    # Find TC records that have duplicate (type, indicator) keys
    duplicate_groups: list[tuple[str, str, list[dict]]] = [
        (type, indicator, tcs)
        for (type, indicator), tcs in tcs_by_indicator.items()
        if len(tcs) > 1
    ]
    if not duplicate_groups:
        logger.info("No duplicate top containers found for Resource ID {resource_id}.")
        return

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
            return

        canonical_tc, duplicate_tcs = _determine_canonical_tc(tcs)

        logger.info(
            f"Identified canonical top container '{canonical_tc['uri']}' "
            f"and {len(duplicate_tcs)} duplicate top container(s): "
            f"{[tc['uri'] for tc in duplicate_tcs]}"
        )

        _handle_duplicate_top_containers(
            aspace_client, canonical_tc, duplicate_tcs, dry_run
        )

    logger.info("Finished merging duplicate top containers")


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
    args = _get_args()
    configure_logging(Path(__file__).stem, args.dry_run)
    config = load_config(args.config_file)
    db_config = config.get("database")
    if not db_config:
        raise ValueError("DB connection settings are required.")
    aspace_client = ASnakeClient(**config)

    _merge_duplicate_containers(
        aspace_client, db_config, args.resource_id, args.dry_run
    )


if __name__ == "__main__":
    main()
