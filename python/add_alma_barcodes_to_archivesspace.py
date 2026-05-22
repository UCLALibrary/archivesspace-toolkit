import argparse
import json

from importlib import import_module
from pathlib import Path
from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient
import asnake.logging as logging

from config.base_match import match_containers
from utils import configure_logging, load_config, read_from_cache, write_to_cache
from utils.alma_utils import get_alma_items_from_alma
from utils.aspace_utils import get_container_refs_from_api, get_container_refs_from_db


# Logger available globally within this module.
# Configuration is done by configure_logging(), which is called by main().
# Made available globally so that tests can use the same logger with their own configuration.
logger = logging.get_logger(Path(__file__).stem)


def _get_args() -> argparse.Namespace:
    """Returns the command-line arguments for this program.

    :return: Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--bib_id", help="Alma bib MMS ID", required=True)
    parser.add_argument("--holdings_id", help="Alma holdings MMS ID", required=True)
    parser.add_argument(
        "--resource_id", help="ArchivesSpace resource ID", required=True
    )
    parser.add_argument(
        "--repo_id",
        help="ArchivesSpace repository ID. Defaults to 2.",
        required=False,
        default=2,
    )
    parser.add_argument("--profile", help="Path to profile module", required=True)
    parser.add_argument(
        "--config_file",
        help="Path to config file with ASpace and Alma info",
        required=True,
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
    parser.add_argument(
        "--undo_barcoding",
        help="Remove barcodes from ASpace for the collection specified by resource_id",
        action="store_true",
    )
    parser.add_argument(
        "--use_log",
        help="Log file to use when undoing barcoding",
    )
    args = parser.parse_args()
    return args


def _get_containers_from_container_refs(
    aspace_client: ASnakeClient, container_refs: set[str]
) -> list[dict]:
    """Returns a list of container data, given a set of container refs.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param set[str] container_refs: A set of container refs.
    :return: A list of containers.
    """
    containers = []
    for tc in container_refs:
        tc_json: dict = aspace_client.get(tc).json()
        # Check that the container is linked to a published resource.
        if not tc_json.get("is_linked_to_published_record"):
            logger.info(
                f"Top container {tc_json.get('uri')} is not linked to a published resource"
            )
            # Skip unlinked containers.
            continue
        containers.append(tc_json)
    return containers


def get_alma_items(
    alma_client: AlmaAPIClient, bib_id: str, holdings_id: str, use_cache: bool
) -> list[dict]:
    """Returns item data from Alma for the given bib_id and holdings_id.
    The data is a list of dictionaries, each containing Alma data for one item.
    Retrieves data from cache file if requested (and if it exists);
    otherwise, retrieves data from Alma.

    :param alma_client: AlmaAPIClient instance.
    :param str bib_id: Bib ID (AKA MMS ID) for the target collection.
    :param str holdings_id: Holdiings ID for the target collection.
    :param bool use_cache: If True, get data from cache file, otherwise get it from Alma.
    :return: A list of dictionaries representing Alma items.
    """
    alma_items = None
    alma_cache_file = f"alma_data_{holdings_id}.json"
    # If using cache, get data from file if it exists.
    if use_cache:
        logger.info(f"Reading Alma data from cache file {alma_cache_file}")
        alma_items = read_from_cache(alma_cache_file)
    # If still no items, retrieve current data from Alma.
    if not alma_items:
        alma_items = get_alma_items_from_alma(alma_client, bib_id, holdings_id)
        # Cache data in file for possible later use.
        logger.info(f"Caching Alma data in {alma_cache_file}")
        write_to_cache(alma_items, alma_cache_file)
    return alma_items


def get_aspace_containers(
    aspace_client: ASnakeClient,
    repo_id: int,
    resource_id: int,
    use_db: bool,
    use_cache: bool,
) -> list[dict]:
    """Given a set of top container ref URIs, obtain the full container data as JSON
    for each one that linked to a published resource.
    Returns a list of qualifying container data.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param int repo_id: ASpace repository ID.
    :param int resource_id: ASpace resource ID for target collection.
    :param bool use_db: If True, get ASpace data from DB, otherwise get it via the API.
    :param bool use_cache: If True, get data from cache file, otherwise get it from Alma.
    :return: A list of containers linked to published resources.
    """
    containers = None
    aspace_cache_file = f"aspace_data_{resource_id}.json"
    # If using cache, get data from file if it exists.
    if use_cache:
        logger.info(f"Reading ASpace data from cache file {aspace_cache_file}")
        containers = read_from_cache(aspace_cache_file)
    # If still no containers, retrieve current data from ASpace.
    if not containers:
        if use_db:
            db_settings = aspace_client.config.get("database")
            container_refs = get_container_refs_from_db(db_settings, resource_id)
        else:
            container_refs = get_container_refs_from_api(
                aspace_client, repo_id, resource_id
            )

        # The top containers endpoint returns refs, so we need to get the full container JSON.
        containers = _get_containers_from_container_refs(aspace_client, container_refs)

        # Cache data in file for possible later use.
        logger.info(f"Caching ASpace data in {aspace_cache_file}")
        write_to_cache(containers, aspace_cache_file)

    return containers


def print_unhandled_data(unhandled_data: dict) -> None:
    """Formats the unhandled data dictionary and prints it to the console.

    :param dict unhandled_data: A dict representing an item of unhandled data.
    """
    # get descriptions of unmatched alma items, and sort them
    unmatched_alma_items = unhandled_data.get("unmatched_alma_items", [])
    unmatched_alma_items_desc = [
        f"{item.get('description')} ({item.get('barcode')})"
        for item in unmatched_alma_items
    ]
    unmatched_alma_items_desc.sort()

    # get indicators of unmatched aspace top containers, and sort them
    unmatched_aspace_containers = unhandled_data.get("unmatched_aspace_containers", [])
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
    top_containers_with_barcodes = unhandled_data.get(
        "top_containers_with_barcodes", []
    )
    top_containers_with_barcodes_desc = [
        f"{tc.get('type')} {tc.get('indicator')} ({tc.get('uri')})"
        for tc in top_containers_with_barcodes
    ]

    # get descriptions of items with duplicate keys, and sort them
    # the format of these lists varies, so output all the data
    items_with_duplicate_keys = unhandled_data.get("items_with_duplicate_keys", [])

    # get descriptions of top containers with duplicate keys, and sort them
    # the format of these lists varies, so output all the data
    tcs_with_duplicate_keys = unhandled_data.get("tcs_with_duplicate_keys", [])

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


def print_summary_info(
    alma_items: list[dict],
    aspace_containers: list[dict],
    matched_aspace_containers: list[dict],
    unhandled_data: dict,
    print_output: bool,
) -> None:
    """Writes summary information about the run to the log.
    If print_output is True, also prints the info to console.

    :param list[dict] alma_items: A list of all Alma items.
    :param list[dict] aspace_containers: A list of all ASpace containers.
    :param list[dict] matched_aspace_containers: A list of matched ASpace containers.
    :param dict unhandled_data: A dict representing unhandled items.
    :param bool print_output: If True, print summary to console, otherwise only write to file.
    """
    summary_info = [
        f"Total Alma items: {len(alma_items)}",
        f"Total ASpace top containers: {len(aspace_containers)}",
        f"Matched ASpace top containers: {len(matched_aspace_containers)}",
        (
            f"ASpace top containers with existing barcodes:"
            f" {len(unhandled_data.get('top_containers_with_barcodes', []))}"
        ),
        f"Unmatched Alma items: {len(unhandled_data.get('unmatched_alma_items', []))}",
        (
            f"Unmatched ASpace top containers:"
            f" {len(unhandled_data.get('unmatched_aspace_containers', []))}"
        ),
        (
            f"Alma items with duplicate keys:"
            f" {len(unhandled_data.get('items_with_duplicate_keys', []))}"
        ),
        (
            f"ASpace top containers with duplicate keys:"
            f" {len(unhandled_data.get('tcs_with_duplicate_keys', []))}"
        ),
    ]
    for message in summary_info:
        logger.info(message)
        if print_output:
            print(message)


def _get_container_refs_from_log_file(filename: str) -> set[str]:
    """Parses a log file and returns refs for containers that had barcodes added to them.

    :param filename: Filename of cache file with ASpace data.
    :return: A set of ASpace top container refs.
    """

    log_file = Path(filename)
    if not log_file.exists():
        print(f"{filename} does not exist. Exiting...")
        raise SystemExit()

    container_refs = []
    with open(log_file, "r") as f:
        for line in f:
            log_item = json.loads(line)
            event = log_item.get("event")
            if event and event.startswith("Added barcode to top container"):
                # parse top container ref from log event
                ref = event.split("Added barcode to top container").pop().strip()
                container_refs.append(ref)
    return set(container_refs)


def _remove_barcodes_from_aspace(
    aspace_client: ASnakeClient, args: argparse.Namespace
) -> None:
    """Removes barcodes in ASpace from top containers related to the provided resource ID.

    :param ASnakeClient aspace_client: ASnakeClient instance for ASpace environment.
    :param argparse.Namespace: CLI arguments for this program.
    """

    if args.use_log:
        container_refs = _get_container_refs_from_log_file(args.use_log)
        print(f"Retrieving container information from {args.use_log}...")
        aspace_containers = _get_containers_from_container_refs(
            aspace_client, container_refs
        )
    else:
        print("Retrieving container information from ASpace...")
        aspace_containers = get_aspace_containers(
            aspace_client=aspace_client,
            repo_id=args.repo_id,
            resource_id=args.resource_id,
            use_db=args.use_db,
            use_cache=args.use_cache,
        )
    # Make sure returned containers have barcodes
    top_containers_with_barcodes = [tc for tc in aspace_containers if tc.get("barcode")]

    if not top_containers_with_barcodes:
        print(
            f"No top containers with barcodes found for Resource ID {args.resource_id}"
        )
        return

    confirmation = input(
        f"Are you sure you want to remove {len(top_containers_with_barcodes)} "
        f"barcodes for Resource ID {args.resource_id} in ArchivesSpace?"
        " (y/N): "
    )

    if confirmation and confirmation.lower() in "yes":
        # Extra confirmation step if log is not used
        if not args.use_log:
            delete_all_warning = input(
                "WARNING! You are about to delete all barcodes"
                f" for the containers related to Resource ID {args.resource_id}."
                " Re-enter the Resource ID to proceed: "
            )
            # Return if extra confirmation is undefined or does not match resource ID
            if not delete_all_warning or not delete_all_warning.strip() == str(
                args.resource_id
            ):
                print("Aborting undo...")
                return

        print(f"Removing barcodes for ASpace Resource ID {args.resource_id}...")
        if args.dry_run:
            message = (
                "Running in dry run mode..."
                f"would remove barcodes from {len(top_containers_with_barcodes)} top containers "
                "in live mode"
            )
            logger.info(message)
            print(message)
            return
        # Delete barcodes using fetched container refs
        for tc in top_containers_with_barcodes:
            logger.info(
                f"Deleted barcode {tc['barcode']} for top container {tc['uri']}"
            )
            del tc["barcode"]
            aspace_client.post(tc["uri"], json=tc)

        message = (
            f"Removed barcodes from {len(top_containers_with_barcodes)} "
            f"top containers related to ASpace Resource ID {args.resource_id}"
        )
        logger.info(message)
        print(message)
        return
    else:
        print("Aborting undo...")
        return


def main() -> None:
    """Add barcodes pulled from Alma records to matching records in ArchivesSpace."""

    # For convenience while debugging, print log name without full container path.
    # Also used in names of some output files.
    logging_filename_base = Path(__file__).stem
    print(f"Logging to {logging_filename_base}.log")
    configure_logging(log_filename_stem=logging_filename_base)

    args: argparse.Namespace = _get_args()
    config = load_config(args.config_file)
    alma_api_key = config["alma_config"]["alma_api_key"]

    alma_client = AlmaAPIClient(alma_api_key)
    aspace_client = ASnakeClient(**config)

    # Require use of --undo_barcoding if --use_log is set
    if args.use_log and not args.undo_barcoding:
        print("The --undo_barcoding is required when --use_log is set")
        return

    if args.undo_barcoding:
        _remove_barcodes_from_aspace(aspace_client, args)
        return

    alma_items = get_alma_items(
        alma_client, args.bib_id, args.holdings_id, args.use_cache
    )
    logger.info(f"Found {len(alma_items)} items in Alma")

    aspace_containers = get_aspace_containers(
        aspace_client, args.repo_id, args.resource_id, args.use_db, args.use_cache
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
    print_summary_info(
        alma_items,
        aspace_containers,
        matched_aspace_containers,
        unhandled_data,
        args.print_output,
    )

    # If print_output is set, print the unhandled data
    # to the console in a readable format.
    if args.print_output:
        print()
        print_unhandled_data(unhandled_data)

    # if there is any unhandled data, write it to a file
    if unhandled_data:
        unhandled_data_filename = f"unhandled_{logging_filename_base}.json"
        write_to_cache(unhandled_data, unhandled_data_filename, indent=2)
        logger.info(
            f"Unhandled data (items and top containers remaining unmatched or with duplicate keys)"
            f" written to {unhandled_data_filename}"
        )


if __name__ == "__main__":
    main()
