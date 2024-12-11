import argparse
import json
from datetime import datetime
from pathlib import Path
from alma_api_keys import API_KEYS
from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient
from structlog.stdlib import BoundLogger  # for typehints
import asnake.logging as logging
from MySQLdb import connect
from MySQLdb.cursors import DictCursor


def _get_logger() -> BoundLogger:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logging_filename_base = f"add_alma_barcodes_to_archivesspace_{timestamp}"
    logging_filename = f"{logging_filename_base}.log"
    logging.setup_logging(filename=logging_filename, level="INFO")
    return logging.get_logger("add_barcodes_to_archivesspace")


def _get_args() -> argparse.Namespace:
    # add args for env, holding_id, ASpace resource_id
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
    # dry run option
    parser.add_argument(
        "--dry_run",
        help="Dry run: do not update top containers in ArchivesSpace",
        action="store_true",
    )
    # print output option
    parser.add_argument(
        "--print_output",
        help="Print output to console in addition to writing to log file",
        action="store_true",
    )
    args = parser.parse_args()
    return args


def _get_alma_api_key(alma_environment: str) -> str:
    if alma_environment == "sandbox":
        alma_api_key = API_KEYS["SANDBOX"]
    elif alma_environment == "production":
        alma_api_key = API_KEYS["DIIT_SCRIPTS"]
    return alma_api_key


def _get_alma_items_from_alma(
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
    # Paramterized query requires tuple of values
    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, (resource_id,))
    container_refs = set(row["container_uri"] for row in cursor.fetchall())
    cursor.close()
    mysql_client.close()
    return container_refs


def get_alma_items(
    alma_client: AlmaAPIClient, bib_id: str, holdings_id: str
) -> list[dict]:
    # EXPERIMENT: Get data from file if it exists
    alma_data_file = Path(f"alma_data_{holdings_id}.json")
    if alma_data_file.exists():
        logger.info(f"Reading alma data from {alma_data_file}")
        with open(alma_data_file, "r") as f:
            alma_items = json.load(f)
    else:
        alma_items = _get_alma_items_from_alma(alma_client, bib_id, holdings_id)
        # EXPERIMENT: Store data in file for possible later use.
        logger.info(f"Writing alma data to {alma_data_file}")
        with open(alma_data_file, "w") as f:
            json.dump(alma_items, f)
    return alma_items


def get_aspace_containers(
    aspace_client: ASnakeClient, resource_id: int, use_db: bool
) -> list[str]:
    """
    Given a set of top container ref URIs, obtain the full container data as JSON
    for each one that linked to a published resource.
    Returns a list of qualifying container data.
    """
    # EXPERIMENT: Get data from file if it exists
    aspace_data_file = Path(f"aspace_data_{resource_id}.json")
    if aspace_data_file.exists():
        logger.info(f"Reading alma data from {aspace_data_file}")
        with open(aspace_data_file, "r") as f:
            containers = json.load(f)
    else:
        if use_db:
            db_settings = aspace_client.config.get("database")
            container_refs = _get_container_refs_from_db(db_settings, resource_id)
        else:
            container_refs = _get_container_refs_from_api(aspace_client, resource_id)

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
        # EXPERIMENT: Store data in file for possible later use.
        logger.info(f"Writing alma data to {aspace_data_file}")
        with open(aspace_data_file, "w") as f:
            json.dump(containers, f)

    return containers


def main() -> None:
    args: argparse.Namespace = _get_args()
    alma_client = AlmaAPIClient(_get_alma_api_key(args.alma_environment))
    aspace_client = ASnakeClient(config_file=args.asnake_config)

    alma_items = get_alma_items(alma_client, args.bib_id, args.holdings_id)
    logger.info(f"Found {len(alma_items)} items in Alma")

    aspace_containers = get_aspace_containers(
        aspace_client, args.resource_id, args.use_db
    )
    logger.info(f"Found {len(aspace_containers)} top containers in ASpace")


if __name__ == "__main__":
    # Defining logger here makes it available to all code in this module.
    logger = _get_logger()
    # For convenience while debugging, print log name without full container path.
    print(f"Logging to {Path(logging.handler.baseFilename).name}")
    # Finally, do everything
    main()
