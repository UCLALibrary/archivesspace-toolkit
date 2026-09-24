import argparse
import asnake.logging as logging

from asnake.client import ASnakeClient
from pathlib import Path

from utils import configure_logging, load_config, write_dicts_to_csv
from utils.aspace_utils import find_false_duplicates, get_required_db_settings

# Logger available globally within this module.
# Configuration is done by configure_logging(), which is called by main().
# Made available globally so that tests can use the same logger with their own configuration.
logger = logging.get_logger(Path(__file__).stem)


def _get_args() -> argparse.Namespace:
    """Returns the command line arguments for the script."""
    parser = argparse.ArgumentParser(
        description="Find duplicate indicators in Alma and ArchivesSpace."
    )
    parser.add_argument(
        "--config_file",
        required=True,
        help="Path to YAML configuration file with ArchivesSpace API and database "
        "credentials.",
    )
    parser.add_argument(
        "--collection_id",
        required=False,
        help="AS collection ID to check for duplicates. If not provided, all eligible collections "
        "will be checked. Do not use with --start_collection_id or --end_collection_id.",
    )
    parser.add_argument(
        "--start_collection_id",
        required=False,
        help="AS collection ID to start checking from. Only collections with IDs greater "
        "than or equal to this will be checked.",
    )
    parser.add_argument(
        "--end_collection_id",
        required=False,
        help="AS collection ID to end checking at. Only collections with IDs less than or equal "
        "to this will be checked.",
    )
    return parser.parse_args()


def get_all_collection_ids(aspace_client: ASnakeClient) -> list[str]:
    """Returns a list of all collection IDs in ArchivesSpace.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    """
    collection_ids = []
    for collection in aspace_client.get_paged("repositories/2/resources"):
        # Get URI, e.g. /repositories/2/resources/123, and extract the numeric ID at the end
        collection_ids.append(collection.get("uri").split("/")[-1])
    return collection_ids


def get_containers_in_collection(
    aspace_client: ASnakeClient, collection_id: str
) -> set[str]:
    """Returns a list of all containers in a given collection.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str collection_id: The numeric ID of the collection to check.
    """
    url = f"/repositories/2/resources/{collection_id}/top_containers"
    container_refs = aspace_client.get(url).json()
    # Extract the ref URIs and de-dup
    return set(tc["ref"] for tc in container_refs)


def get_collection_title(aspace_client: ASnakeClient, collection_id: str) -> str:
    """Returns the title of a collection given its ID.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str collection_id: The numeric ID of the collection to check.
    """
    url = f"/repositories/2/resources/{collection_id}"
    collection = aspace_client.get(url).json()
    return collection.get("title")


def get_top_container(aspace_client: ASnakeClient, container_uri: str) -> dict:
    """Returns the full top container record for a URI.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str container_uri: The URI of the container to retrieve.
    """
    return aspace_client.get(container_uri).json()


def get_location_titles(aspace_client: ASnakeClient, container: dict) -> list[str]:
    """Returns a list of names for the locations linked to a container.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict container: Full top container record.
    """
    full_locations = []
    for loc in container.get("container_locations", []):
        if "ref" in loc:
            location = aspace_client.get(loc["ref"]).json()
            full_locations.append(location.get("title", "Unknown Location"))
    return full_locations


def _indicator_sort_key(indicator: str | None) -> tuple:
    """Sort numeric indicators numerically, before any non-numeric ones."""
    indicator = indicator or ""
    # For each indicator, construct a tuple that allows proper sorting, formatted as:
    # (0, numeric_value, "") for numeric indicators, or
    # (1, 0, indicator) for non-numeric indicators.
    # This ensures that numeric indicators are always sorted before non-numeric ones,
    # and within each group, they are sorted appropriately.
    if indicator.isdigit():
        return (0, int(indicator), "")
    else:
        return (1, 0, indicator)


def write_duplicates_to_file(
    duplicates: list[dict], filename: str, base_url: str
) -> None:
    """Writes a list of duplicate indicators to a CSV file.

    :param list[dict] duplicates: A list of dictionaries with keys 'collection', 'indicator',
    'type', 'container_uri', 'locations', 'false_duplicate', and 'note'.
    :param str filename: The name of the CSV file to write to.
    :param str base_url: The base URL of the ArchivesSpace instance, used to create links to TCs.
    """
    # Sort by collection, then type, then indicator, then container URI for easier reading.
    # Make sure indicator sort is done numerically, not by string comparison
    duplicates.sort(
        key=lambda x: (
            x["collection"] or "",
            x["type"] or "",
            _indicator_sort_key(x["indicator"]),
            x["container_uri"],
        )
    )

    for item in duplicates:
        # Add a new column for the link to the TC in ArchivesSpace.
        item["tc_link"] = format_tc_uri_as_link(item["container_uri"], base_url)
        # Concatenate location names into a single string for easier reading in the CSV.
        item["locations"] = "; ".join(item["locations"])

    write_dicts_to_csv(Path(filename), duplicates)


def format_tc_uri_as_link(uri: str, base_url: str) -> str:
    """Formats an ArchivesSpace Top Container URI as link to the TC in ArchivesSpace.

    :param str uri: The ArchivesSpace Top Container URI to format.
    :param str base_url: The base URL of the ArchivesSpace instance.
    """
    # TC URIs look like /repositories/2/top_containers/123 -
    # We want the last two parts for the link (e.g. "top_containers/123")
    tc_path = "/".join(uri.split("/")[-2:])
    # Base URL mayl end with a port (e.g. :1234) and possibly "/api",
    # so remove everything after the last colon if it's not followed by two slashes
    port_split = base_url.rsplit(":", 1)
    if len(port_split) == 2 and not port_split[1].startswith("//"):
        base_url = port_split[0]
    return f"{base_url}/{tc_path}"


def main() -> None:
    configure_logging(Path(__file__).stem)
    args = _get_args()

    # Get URL info from config file, and initialize client
    config = load_config(args.config_file)
    # Required to correctly identify false duplicates.
    db_settings = get_required_db_settings(config)
    base_url = config.get("baseurl", "")
    aspace_client = ASnakeClient(**config)

    # Check that provided start, end, and specific collection IDs make sense together.
    # If collection_id is provided, we should not have start_collection_id or end_collection_id.
    # If we have both start_collection_id and end_collection_id, check that
    # start_collection_id <= end_collection_id.
    # Providing only one of start_collection_id or end_collection_id is supported.
    if args.collection_id and (args.start_collection_id or args.end_collection_id):
        logger.error(
            "Cannot use --collection_id together with --start_collection_id or --end_collection_id."
        )
        return
    elif args.start_collection_id and args.end_collection_id:
        if int(args.start_collection_id) > int(args.end_collection_id):
            logger.error(
                "start_collection_id must be less than or equal to end_collection_id."
            )
            return

    # Get collections to check
    if args.collection_id:
        collection_ids = [args.collection_id]
    else:
        collection_ids = get_all_collection_ids(aspace_client)
        # If start_collection_id or end_collection_id provided, filter the list of IDs to check
        if args.start_collection_id:
            collection_ids = [
                cid
                for cid in collection_ids
                if int(cid) >= int(args.start_collection_id)
            ]

        if args.end_collection_id:
            collection_ids = [
                cid for cid in collection_ids if int(cid) <= int(args.end_collection_id)
            ]

    logger.info(f"Checking {len(collection_ids)} collections for duplicate indicators.")

    tcs_with_duplicates = []
    for collection_id in collection_ids:
        collection_title = get_collection_title(aspace_client, collection_id)
        logger.info(
            f"Checking collection {collection_title} (ID: {collection_id}) for duplicates."
        )
        # Get all containers in the collection
        container_refs = get_containers_in_collection(aspace_client, collection_id)
        logger.info(
            f"Found {len(container_refs)} containers in collection "
            f"{collection_title} (ID: {collection_id})."
        )
        # Index all containers by their indicator and type:
        # Create a dictionary where the key is a tuple of (indicator, type)
        # and the value is a list of full container records with that indicator and type.
        indicator_type_pairs_seen: dict[tuple, list[dict]] = {}
        for container_ref in container_refs:
            container = get_top_container(aspace_client, container_ref)
            key = (container.get("indicator"), container.get("type"))
            indicator_type_pairs_seen.setdefault(key, []).append(container)

        for (tc_indicator, tc_type), containers in indicator_type_pairs_seen.items():
            if len(containers) < 2:
                continue
            # Flag, but do not hide, "false duplicates": placeholder containers
            # for backlog / accession material.
            false_duplicates = find_false_duplicates(db_settings, containers)
            real_count = len(containers) - len(false_duplicates)
            logger.warning(
                f"Duplicate indicator found: {tc_type} {tc_indicator} "
                f"in collection {collection_id} ({len(containers)} occurrences, "
                f"{len(false_duplicates)} false duplicates)"
            )
            for container in containers:
                uri = container.get("uri", "")
                reason = false_duplicates.get(uri, "")
                if reason:
                    logger.info(f"Container {uri} is a false duplicate: {reason}")
                tcs_with_duplicates.append(
                    {
                        "collection": collection_title,
                        "indicator": tc_indicator,
                        "type": tc_type,
                        "container_uri": uri,
                        "locations": get_location_titles(aspace_client, container),
                        "false_duplicate": "Yes" if reason else "",
                        "note": reason,
                        "real_containers_in_group": real_count,
                    }
                )
    # If any duplicates found, write to file. Otherwise log that no duplicates found.
    # Construct a filename based on collection ID(s) included in the report.
    if tcs_with_duplicates:
        if args.collection_id:
            output_filename = (
                f"duplicate_indicators_collection_{args.collection_id}.csv"
            )
        elif args.start_collection_id and args.end_collection_id:
            output_filename = (
                f"duplicate_indicators_collections_{args.start_collection_id}_to_"
                f"{args.end_collection_id}.csv"
            )
        elif args.start_collection_id:
            output_filename = (
                "duplicate_indicators_collections_"
                + f"{args.start_collection_id}_and_up.csv"
            )

        elif args.end_collection_id:
            output_filename = (
                f"duplicate_indicators_collections_up_to_{args.end_collection_id}.csv"
            )
        else:
            output_filename = "duplicate_indicators_all_collections.csv"

        write_duplicates_to_file(tcs_with_duplicates, output_filename, base_url)
        logger.info(f"Duplicate indicators written to {output_filename}")
    else:
        logger.info("No duplicate indicators found.")


if __name__ == "__main__":
    main()
