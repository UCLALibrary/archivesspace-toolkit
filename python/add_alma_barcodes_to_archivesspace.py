import argparse
import json
from datetime import datetime
from importlib import import_module
from pathlib import Path
from alma_api_keys import API_KEYS
from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient
from structlog.stdlib import BoundLogger  # for typehints
import asnake.logging as logging
from MySQLdb import connect
from MySQLdb.cursors import DictCursor
from config.base_match import match_containers


def _get_logger(name: str | None = None) -> BoundLogger:
    """
    Returns a logger for the current application. This is provided by
    the asnake.logging package, which uses structlog.
    A unique log filename is created using the current time, and log messages
    will use the name in the 'logger' field.
    If name not supplied, the name of the current script is used.
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
    """Returns the command-line arguments for this program."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--alma_environment",
        help="Alma environment (sandbox or production)",
        choices=["sandbox", "production"],
        required=True,
    )
    parser.add_argument("--bib_id", help="Alma bib MMS ID", required=True)
    parser.add_argument("--holdings_id", help="Alma holdings MMS ID", required=True)
    parser.add_argument(
        "--resource_id", help="ArchivesSpace resource ID", required=True
    )
    parser.add_argument("--profile", help="Path to profile module", required=True)
    parser.add_argument(
        "--asnake_config", help="Path to ArchivesSnake config file", required=True
    )
    parser.add_argument(
        "--use_db",
        help="Get containers from database instead of API",
        action="store_true",
    )
    parser.add_argument(
        "--dry_run",
        help="Dry run: do not update top containers in ArchivesSpace",
        action="store_true",
    )
    parser.add_argument(
        "--print_output",
        help="Print output to console in addition to writing to log file",
        action="store_true",
    )
    parser.add_argument(
        "--use_cache",
        help="Read Alma and ASpace data from cached files (if available)",
        action="store_true",
    )
    args = parser.parse_args()
    return args


def _get_alma_api_key(alma_environment: str) -> str:
    """Returns the Alma API key associated with the given environment."""
    if alma_environment == "sandbox":
        alma_api_key = API_KEYS["SANDBOX"]
    elif alma_environment == "production":
        alma_api_key = API_KEYS["DIIT_SCRIPTS"]
    return alma_api_key


def _get_alma_items_from_alma(
    alma_client: AlmaAPIClient, bib_id: str, holdings_id: str
) -> list[dict]:
    """
    Returns item data from Alma for the given bib_id and holdings_id.
    The data is a list of dictionaries, each containing Alma data for one item.
    """
    alma_items = []
    offset = 0
    # get the total expected number of items
    total_items = alma_client.get_items(bib_id, holdings_id, {"limit": 1}).get(
        "total_record_count"
    )
    while len(alma_items) < total_items:
        current_items = alma_client.get_items(
            bib_id, holdings_id, {"limit": 100, "offset": offset}
        )
        offset += 100
        # keep only item_data from each item in the list
        for item in current_items.get("item"):
            alma_items.append(item.get("item_data"))
    return alma_items


def _get_container_refs_from_api(
    aspace_client: ASnakeClient, resource_id: int
) -> set[str]:
    """
    Returns a de-duped set of _ref_ top container URIs for the given resource_id,
    obtained via API call.
    This API call can fail via timeout in hosted environments, when
    more than a few thousand containers are associated with the resource.
    """
    url = f"/repositories/2/resources/{resource_id}/top_containers"
    container_refs = aspace_client.get(url).json()
    # Extract the ref URIs and de-dup.
    return set(tc["ref"] for tc in container_refs)


def _get_container_refs_from_db(db_settings: dict, resource_id: int) -> set[str]:
    """
    Returns a de-duped set of _ref_ top container URIs for the given resource_id,
    obtained via database query.
    This is intended as an alternative for resources with more than a few thousand
    containers, as the API call may time out.
    """
    mysql_client = connect(
        host=db_settings.get("host"),
        database=db_settings.get("database"),
        user=db_settings.get("user"),
        password=db_settings.get("password"),
    )

    query = """
        select distinct
            concat('/repositories/', r.repo_id, '/top_containers/', tc.id) as container_uri
        from resource r
        inner join archival_object ao on r.id = ao.root_record_id
        inner join instance i on ao.id = i.archival_object_id
        inner join sub_container sc on i.id = sc.instance_id
        inner join top_container_link_rlshp tclr on sc.id = tclr.sub_container_id
        inner join top_container tc on tclr.top_container_id = tc.id
        where r.id = %s
        and ao.publish = 1 -- true
        and ao.suppressed = 0 -- false
        order by container_uri
    """
    # Parameterized query requires tuple of values
    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, (resource_id,))
    container_refs = set(row["container_uri"] for row in cursor.fetchall())
    cursor.close()
    mysql_client.close()
    return container_refs


def _get_containers_from_container_refs(
    aspace_client: ASnakeClient, container_refs: set[str]
) -> list[str]:
    containers = []
    for tc in container_refs:
        tc_json = aspace_client.get(tc).json()
        # Check that the container is linked to a published resource.
        if not tc_json.get("is_linked_to_published_record"):
            logger.info(
                f"Top container {tc_json.get('uri')} is not linked to a published resource"
            )
            # Skip unlinked containers.
            continue
        containers.append(tc_json)
    return containers


def _get_cached_data_from_file(filename: str) -> list[dict]:
    """
    Reads data from the given file and returns it.
    For this project, data will be list[dict], but this method does not enforce that.
    """
    data_file = Path(filename)
    if data_file.exists():
        logger.info(f"Reading alma data from {data_file}")
        with open(data_file, "r") as f:
            data = json.load(f)
    else:
        data = None
    return data


def _store_cached_data_in_file(data: list[dict], filename: str) -> None:
    """
    Stores data in the given file for possible later use.
    For this project, data will be list[dict], but this method does not enforce that.
    """
    logger.info(f"Writing data to {filename}")
    with open(filename, "w") as f:
        json.dump(data, f)


def get_alma_items(
    alma_client: AlmaAPIClient, bib_id: str, holdings_id: str, use_cache: bool
) -> list[dict]:
    """
    Returns item data from Alma for the given bib_id and holdings_id.
    The data is a list of dictionaries, each containing Alma data for one item.
    Retrieves data from cache file if requested (and if it exists);
    otherwise, retrieves data from Alma.
    """
    alma_items = None
    alma_cache_file = f"alma_data_{holdings_id}.json"
    # If using cache, get data from file if it exists.
    if use_cache:
        alma_items = _get_cached_data_from_file(alma_cache_file)
    # If still no items, retrieve current data from Alma.
    if not alma_items:
        alma_items = _get_alma_items_from_alma(alma_client, bib_id, holdings_id)
        # Cache data in file for possible later use.
        _store_cached_data_in_file(alma_items, alma_cache_file)
    return alma_items


def get_aspace_containers(
    aspace_client: ASnakeClient, resource_id: int, use_db: bool, use_cache: bool
) -> list[str]:
    """
    Given a set of top container ref URIs, obtain the full container data as JSON
    for each one that linked to a published resource.
    Returns a list of qualifying container data.
    """
    containers = None
    aspace_cache_file = f"aspace_data_{resource_id}.json"
    # If using cache, get data from file if it exists.
    if use_cache:
        containers = _get_cached_data_from_file(aspace_cache_file)
    # If still no containers, retrieve current data from ASpace.
    if not containers:
        if use_db:
            db_settings = aspace_client.config.get("database")
            container_refs = _get_container_refs_from_db(db_settings, resource_id)
        else:
            container_refs = _get_container_refs_from_api(aspace_client, resource_id)

        # The top containers endpoint returns refs, so we need to get the full container JSON.
        containers = _get_containers_from_container_refs(aspace_client, container_refs)

        # Cache data in file for possible later use.
        _store_cached_data_in_file(containers, aspace_cache_file)

    return containers


def write_json_to_file(data: list[dict], filename: str) -> None:
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)


def print_unhandled_data(unhandled_data: dict) -> None:
    """
    Formats the unhandled data dictionary and prints it to the console.
    """
    # get descriptions of unmatched alma items, and sort them
    unmatched_alma_items = unhandled_data.get("unmatched_alma_items")
    unmatched_alma_items_desc = [
        f"{item.get('description')} ({item.get('barcode')})"
        for item in unmatched_alma_items
    ]
    unmatched_alma_items_desc.sort()

    # get indicators of unmatched aspace top containers, and sort them
    unmatched_aspace_containers = unhandled_data.get("unmatched_aspace_containers")
    # we'll want to sort the indicators as numbers, not strings
    # so add a sort key to the list of top containers
    # the sort key is the integer value of the indicator, or 0 if it's not a number
    # (so it will sort to the top)
    unmatched_aspace_containers_desc = [
        [
            int(tc.get("indicator")) if tc.get("indicator").isdigit() else 0,
            f"{tc.get('type')} {tc.get('indicator')} ({tc.get('uri')})",
        ]
        for tc in unmatched_aspace_containers
    ]

    unmatched_aspace_containers_desc.sort()
    # remove the sort key
    unmatched_aspace_containers_desc = [
        tc[1] for tc in unmatched_aspace_containers_desc
    ]

    # get descriptions of top containers with existing barcodes, and sort them
    top_containers_with_barcodes = unhandled_data.get("top_containers_with_barcodes")
    top_containers_with_barcodes_desc = [
        f"{tc.get('type')} {tc.get('indicator')} ({tc.get('uri')})"
        for tc in top_containers_with_barcodes
    ]

    # get descriptions of items with duplicate keys, and sort them
    # the format of these lists varies, so output all the data
    items_with_duplicate_keys = unhandled_data.get("items_with_duplicate_keys")

    # get descriptions of top containers with duplicate keys, and sort them
    # the format of these lists varies, so output all the data
    tcs_with_duplicate_keys = unhandled_data.get("tcs_with_duplicate_keys")

    # Print the data.
    print("Unhandled data:\n" "Unmatched Alma items:\n")
    for item in unmatched_alma_items_desc:
        print(f"{item}\n")
    print("\nUnmatched ASpace top containers:\n")
    for tc in unmatched_aspace_containers_desc:
        print(f"{tc}\n")
    print("\nASpace top containers with existing barcodes:\n")
    for tc in top_containers_with_barcodes_desc:
        print(f"{tc}\n")
    print("\nAlma items with duplicate keys:\n")
    for item in items_with_duplicate_keys:
        print(f"{item}\n")
    print("\nASpace top containers with duplicate keys:\n")
    for tc in tcs_with_duplicate_keys:
        print(f"{tc}\n")


def get_run_summary_info(
    alma_items: list[dict],
    aspace_containers: list[dict],
    matched_aspace_containers: list[dict],
    unhandled_data: dict,
) -> list[str]:
    """
    Returns a list of strings with summary information about the run.
    """
    summary_info = [
        f"Total Alma items: {len(alma_items)}",
        f"Total ASpace top containers: {len(aspace_containers)}",
        f"Matched ASpace top containers: {len(matched_aspace_containers)}",
        (
            f"ASpace top containers with existing barcodes:"
            f" {len(unhandled_data.get('top_containers_with_barcodes'))}"
        ),
        f"Unmatched Alma items: {len(unhandled_data.get('unmatched_alma_items'))}",
        (
            f"Unmatched ASpace top containers:"
            f" {len(unhandled_data.get('unmatched_aspace_containers'))}"
        ),
        f"Alma items with duplicate keys: {len(unhandled_data.get('items_with_duplicate_keys'))}",
        (
            f"ASpace top containers with duplicate keys:"
            f" {len(unhandled_data.get('tcs_with_duplicate_keys'))}"
        ),
    ]
    return summary_info


def main() -> None:
    # For convenience while debugging, print log name without full container path.
    # Also used in names of some output files.
    logging_filename_base = Path(logging.handler.baseFilename).stem
    print(f"Logging to {logging_filename_base}.log")

    args: argparse.Namespace = _get_args()
    alma_client = AlmaAPIClient(_get_alma_api_key(args.alma_environment))
    aspace_client = ASnakeClient(config_file=args.asnake_config)

    logger.info(f"Using Alma API key for {args.alma_environment} environment")
    alma_items = get_alma_items(
        alma_client, args.bib_id, args.holdings_id, args.use_cache
    )
    logger.info(f"Found {len(alma_items)} items in Alma")

    aspace_containers = get_aspace_containers(
        aspace_client, args.resource_id, args.use_db, args.use_cache
    )
    logger.info(f"Found {len(aspace_containers)} top containers in ASpace")

    # TODO: Refactor below here, including config and data reporting code.

    # Load profile module
    profile_module = import_module(args.profile)
    get_alma_match_data = getattr(profile_module, "get_alma_match_data")
    get_aspace_match_data = getattr(profile_module, "get_aspace_match_data")

    # find top containers with existing barcodes
    # add them to a list for later output and remove them from the list of ASpace containers
    top_containers_with_barcodes = [tc for tc in aspace_containers if tc.get("barcode")]
    if top_containers_with_barcodes:
        aspace_containers = [
            tc for tc in aspace_containers if tc not in top_containers_with_barcodes
        ]

    # get match data for Alma items and ASpace top containers
    aspace_match_data, tcs_with_duplicate_keys = get_aspace_match_data(
        aspace_containers, logger
    )
    alma_match_data, items_with_duplicate_keys = get_alma_match_data(alma_items, logger)
    # match Alma items with ASpace top containers
    matched_aspace_containers, unhandled_data = match_containers(
        alma_match_data,
        aspace_match_data,
        logger,
    )

    # add top containers with existing barcodes to unhandled data for output
    unhandled_data["top_containers_with_barcodes"] = top_containers_with_barcodes
    # add items and top containers with duplicate keys to unhandled data for output
    unhandled_data["items_with_duplicate_keys"] = items_with_duplicate_keys
    unhandled_data["tcs_with_duplicate_keys"] = tcs_with_duplicate_keys

    # update ASpace top containers with barcodes - only if not a dry run
    if args.dry_run:
        logger.info("Dry run: no changes made to ASpace top containers")

    else:
        for tc in matched_aspace_containers:
            aspace_client.post(tc["uri"], json=tc)
            logger.info(f"Added barcode to top container {tc['uri']}")

        logger.info(
            f"Updated barcodes for {len(matched_aspace_containers)} top containers"
        )

    # summary outputs: total number of items and top containers,
    # and numbers of unhanded items and top containers
    run_summary = get_run_summary_info(
        alma_items, aspace_containers, matched_aspace_containers, unhandled_data
    )
    for message in run_summary:
        logger.info(message)

    # if print_output is set, print the run summary and unhandled data
    # to the console in a readable format
    if args.print_output:
        for message in run_summary:
            print(message)
        print()
        print_unhandled_data(unhandled_data)

    # if there is any unhandled data, write it to a file
    if unhandled_data:
        unhandled_data_filename = f"unhandled_{logging_filename_base}.json"
        write_json_to_file(unhandled_data, unhandled_data_filename)
        logger.info(
            f"Unhandled data (items and top containers remaining unmatched or with duplicate keys)"
            f" written to {unhandled_data_filename}"
        )


if __name__ == "__main__":
    # Defining logger here makes it available to all code in this module.
    logger = _get_logger()
    # Finally, do everything
    main()
