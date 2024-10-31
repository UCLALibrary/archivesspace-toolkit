import argparse
import json
from datetime import datetime
from importlib import import_module
from alma_api_keys import API_KEYS
from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient
from MySQLdb import connect, Connection
from MySQLdb.cursors import DictCursor
import asnake.logging as logging


timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
logging_filename_base = f"add_alma_barcodes_to_archivesspace_{timestamp}"
logging_filename = f"{logging_filename_base}.log"
logging.setup_logging(filename=logging_filename, level="INFO")
# set label for custom logger - all output will be in archivessnake.log
logger = logging.get_logger("add_barcodes_to_archivesspace")


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


def _get_container_refs_from_db(mysql_client: Connection, resource_id: int) -> set[str]:
    """
    Returns a de-duped set of _ref_ top container URIs for the given resource_id,
    obtained via database query.
    This is intended as an alternative for resources with more than a few thousand
    containers, as the API call may time out.
    """
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
    # Paramterized query requires tuple of values
    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, (resource_id,))
    container_refs = set(row["container_uri"] for row in cursor.fetchall())
    cursor.close()
    return container_refs


def get_aspace_containers(container_refs: set[str]) -> list[str]:
    """
    Given a set of top container ref URIs, obtain the full container data as JSON
    for each one that linked to a published resource.
    Returns a list of qualifying container data.
    """
    # the top containers endpoint returns refs, so we need to get the full container JSON
    containers = []
    for tc in container_refs:
        tc_json = aspace_client.get(tc).json()
        # check that the container is linked to a published resource
        if not tc_json.get("is_linked_to_published_record"):
            logger.info(
                f"Top container {tc_json.get('uri')} is not linked to a published resource"
            )
            # skip this container
            continue
        containers.append(tc_json)
    return containers


def get_alma_items(
    alma_client: AlmaAPIClient, bib_id: str, holdings_id: str
) -> list[dict]:
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


def write_json_to_file(data: list[dict], filename: str) -> None:
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    # add args for env, holding_id, ASpace resource_id
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--alma_environment",
        help="Alma environment (sandbox or production)",
        choices=["sandbox", "production"],
    )
    parser.add_argument("--bib_id", help="Alma bib MMS ID")
    parser.add_argument("--holdings_id", help="Alma holdings MMS ID")
    parser.add_argument("--resource_id", help="ArchivesSpace resource ID")
    parser.add_argument("--profile", help="Path to profile module")
    parser.add_argument("--asnake_config", help="Path to ArchivesSnake config file")
    parser.add_argument(
        "--use_db",
        help="Get containers from database instead of API",
        action="store_true",
    )
    args = parser.parse_args()

    if args.alma_environment == "sandbox":
        alma_api_key = API_KEYS["SANDBOX"]
    elif args.alma_environment == "production":
        alma_api_key = API_KEYS["DIIT_SCRIPTS"]

    # load profile module
    profile_module = import_module(args.profile)
    match_containers = getattr(profile_module, "match_containers")

    logger.info(f"Getting ASpace top containers for resource {args.resource_id}")
    aspace_client = ASnakeClient(config_file=args.asnake_config)
    if args.use_db:
        # Initialize database connection, if used;
        # db connection info comes from asnake config file.
        # This is a dictionary of values.
        db_settings = aspace_client.config.get("database")
        mysql_client = connect(
            host=db_settings.get("host"),
            database=db_settings.get("database"),
            user=db_settings.get("user"),
            password=db_settings.get("password"),
        )
        container_refs = _get_container_refs_from_db(mysql_client, args.resource_id)
        mysql_client.close()
    else:
        container_refs = _get_container_refs_from_api(aspace_client, args.resource_id)

    aspace_containers = get_aspace_containers(container_refs)
    logger.info(f"Found {len(aspace_containers)} top containers in ASpace")

    x = 1 / 0
    logger.info(f"Using Alma API key for {args.alma_environment} environment")
    alma_client = AlmaAPIClient(alma_api_key)
    alma_items = get_alma_items(alma_client, args.bib_id, args.holdings_id)
    logger.info(f"Found {len(alma_items)} items in Alma")

    # find top containers with existing barcodes
    # add them to a list for later output and remove them from the list of ASpace containers
    top_containers_with_barcodes = [tc for tc in aspace_containers if tc.get("barcode")]
    if top_containers_with_barcodes:
        aspace_containers = [
            tc for tc in aspace_containers if tc not in top_containers_with_barcodes
        ]

    matched_aspace_containers, unhandled_data = match_containers(
        alma_items, aspace_containers, logger
    )

    # add top containers with existing barcodes to unhandled data for output
    unhandled_data["top_containers_with_barcodes"] = top_containers_with_barcodes

    # update ASpace top containers with barcodes
    for tc in matched_aspace_containers:
        aspace_client.post(tc["uri"], json=tc)
        logger.info(f"Added barcode to top container {tc['uri']}")

    logger.info(f"Updated barcodes for {len(matched_aspace_containers)} top containers")

    # summary outputs: total number of items and top containers,
    # and numbers of unhanded items and top containers
    logger.info(f"Total Alma items: {len(alma_items)}")
    logger.info(f"Total ASpace top containers: {len(aspace_containers)}")
    logger.info(f"Matched ASpace top containers: {len(matched_aspace_containers)}")
    logger.info(
        f"ASpace top containers with existing barcodes:"
        f" {len(unhandled_data.get('top_containers_with_barcodes'))}"
    )
    logger.info(
        f"Unmatched Alma items: {len(unhandled_data.get('unmatched_alma_items'))}"
    )
    logger.info(
        f"Unmatched ASpace top containers:"
        f" {len(unhandled_data.get('unmatched_aspace_containers'))}"
    )
    logger.info(
        f"Alma items with duplicate keys:"
        f" {len(unhandled_data.get('items_with_duplicate_keys'))}"
    )
    logger.info(
        f"ASpace top containers with duplicate keys:"
        f" {len(unhandled_data.get('tcs_with_duplicate_keys'))}"
    )

    # if there is any unhandled data, write it to a file
    if unhandled_data:
        unhandled_data_filename = f"unhandled_{logging_filename_base}.json"
        write_json_to_file(unhandled_data, unhandled_data_filename)
        logger.info(
            f"Unhandled data (items and top containers remaining unmatched or with duplicate keys)"
            f" written to {unhandled_data_filename}"
        )
