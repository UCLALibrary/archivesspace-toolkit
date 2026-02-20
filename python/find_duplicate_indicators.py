import argparse
from datetime import datetime
from pathlib import Path
from asnake.client import ASnakeClient
from structlog.stdlib import BoundLogger  # for typehints
import asnake.logging as logging
import csv
import yaml


def _get_logger(name: str | None = None) -> BoundLogger:
    """Returns a logger for the current application. This is provided by
    the asnake.logging package, which uses structlog.
    A unique log filename is created using the current time, and log messages
    will use the name in the 'logger' field.
    If name not supplied, the name of the current script is used.

    :param str name: Filename for the log. Defaults to None.
    """
    if not name:
        # Use base filename of current script.
        name = Path(__file__).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logging_filename_base = f"{name}_{timestamp}"
    logging_filename = f"{logging_filename_base}.log"
    logging.setup_logging(filename=logging_filename, level="INFO")
    return logging.get_logger(name=name)


def _get_args() -> argparse.Namespace:
    """Returns the command line arguments for the script."""
    parser = argparse.ArgumentParser(
        description="Find duplicate indicators in Alma and ArchivesSpace."
    )
    parser.add_argument(
        "--config_file",
        required=True,
        help="Path to YAML configuration file with ArchivesSpace credentials.",
    )
    parser.add_argument(
        "--collection_id",
        required=False,
        help="AS collection ID to check for duplicates. "
        "If not provided, all collections will be checked.",
    )
    return parser.parse_args()


def get_all_collection_ids(aspace_client: ASnakeClient) -> list[str]:
    """Returns a list of all collection IDs in ArchivesSpace.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    """
    collection_ids = []
    for collection in aspace_client.get_paged("repositories/2/collections"):
        collection_ids.append(collection["uri"])
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


def get_indicator_and_type_from_container_uri(
    aspace_client: ASnakeClient, container_uri: str
) -> tuple[str, str]:
    """Given a container URI, returns the indicator and type.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str container_uri: The URI of the container to retrieve.
    """
    container = aspace_client.get(container_uri).json()
    tc_indicator = container.get("indicator")
    tc_type = container.get("type")
    return tc_indicator, tc_type


def write_duplicates_to_file(
    duplicates: list[dict], filename: str, base_url: str
) -> None:
    """Writes a list of duplicate indicators to a CSV file.

    :param list[dict] duplicates: A list of dictionaries with keys 'collection', 'indicator',
    'type', and 'container_uri'.
    :param str filename: The name of the CSV file to write to.
    :param str base_url: The base URL of the ArchivesSpace instance, used to create links to TCs.
    """
    # Sort by collection, then indicator, then type, then container URI for easier reading.
    duplicates.sort(
        key=lambda x: (x["collection"], x["indicator"], x["type"], x["container_uri"])
    )
    # Add a new column for the link to the TC in ArchivesSpace.
    for item in duplicates:
        item["tc_link"] = format_tc_uri_as_link(item["container_uri"], base_url)

    with open(filename, "w", newline="") as csvfile:
        fieldnames = ["collection", "indicator", "type", "container_uri", "tc_link"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for item in duplicates:
            writer.writerow(item)


def format_tc_uri_as_link(uri: str, base_url: str) -> str:
    """Formats an ArchivesSpace Top Container URI as link to the TC in ArchivesSpace.

    :param str uri: The ArchivesSpace Top Container URI to format.
    :param str base_url: The base URL of the ArchivesSpace instance.
    """
    # TC URIs look like /repositories/2/top_containers/123 -
    # We want the last two parts for the link (e.g. "top_containers/123")
    tc_path = "/".join(uri.split("/")[-2:])
    # Base URL will end with a port (:1234) and possibly "/api", which needs to be removed
    if base_url.endswith("/api"):
        base_url = base_url[:-4]
    if ":" in base_url:
        base_url = base_url.split(":")[0]
    full_url = f"{base_url}/{tc_path}"
    return full_url


def main() -> None:
    args = _get_args()
    logger = _get_logger()

    # Get URL info from config file, and initialize client
    with open(args.config_file, "r") as f:
        config = yaml.safe_load(f)
        base_url = config.get("baseurl")
    aspace_client = ASnakeClient(config_file=args.config_file)

    # Get collections to check
    if args.collection_id:
        collection_ids = [args.collection_id]
    else:
        collection_ids = get_all_collection_ids(aspace_client)

    logger.info(f"Checking {len(collection_ids)} collections for duplicate indicators.")

    tcs_with_duplicates = []
    for collection_id in collection_ids:
        collection_name = get_collection_title(aspace_client, collection_id)
        logger.info(
            f"Checking collection {collection_name} (ID: {collection_id}) for duplicates."
        )
        # Get all containers in the collection
        container_refs = get_containers_in_collection(aspace_client, collection_id)
        logger.info(
            f"Found {len(container_refs)} containers in collection {collection_id}."
        )
        indicator_type_pairs_seen = {}

        # Index all containers by their indicator and type:
        # Create a dictionary where the key is a tuple of (indicator, type)
        # and the value is a list of container URIs that have that indicator and type
        for container_ref in container_refs:
            tc_indicator, tc_type = get_indicator_and_type_from_container_uri(
                aspace_client, container_ref
            )
            key = (tc_indicator, tc_type)
            if key not in indicator_type_pairs_seen:
                indicator_type_pairs_seen[key] = []
            indicator_type_pairs_seen[key].append(container_ref)

        # After collecting all, find duplicates (i.e. keys with more than one container URI)
        for (
            tc_indicator,
            tc_type,
        ), container_uri_list in indicator_type_pairs_seen.items():
            if len(container_uri_list) > 1:
                logger.warning(
                    f"Duplicate indicator found: {tc_type} {tc_indicator} "
                    f"in collection {collection_id} ({len(container_uri_list)} occurrences)"
                )
                for container_ref in container_uri_list:
                    tcs_with_duplicates.append(
                        {
                            "collection": collection_name,
                            "indicator": tc_indicator,
                            "type": tc_type,
                            "container_uri": container_ref,
                        }
                    )

    if tcs_with_duplicates:
        if args.collection_id:
            output_filename = (
                f"duplicate_indicators_collection_{args.collection_id}.csv"
            )
        else:
            output_filename = "duplicate_indicators_all_collections.csv"
        write_duplicates_to_file(tcs_with_duplicates, output_filename, base_url)
        logger.info(f"Duplicate indicators written to {output_filename}")
    else:
        logger.info("No duplicate indicators found.")


if __name__ == "__main__":
    main()
