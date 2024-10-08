import argparse
import json
from importlib import import_module
from alma_api_keys import API_KEYS
from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient
import asnake.logging as logging


logging.setup_logging(filename="archivessnake.log", level="INFO")
# set label for custom logger - all output will be in archivessnake.log
logger = logging.get_logger("add_barcodes_to_archivesspace")


def get_aspace_containers(aspace_client: ASnakeClient, resource_id: int) -> list[str]:
    url = f"/repositories/2/resources/{resource_id}/top_containers"
    container_refs = []
    for tc in aspace_client.get_paged(url):
        container_refs.append(tc)
    # this endpoint returns refs, so we need to get the full container JSON
    containers = []
    for tc in container_refs:
        tc_json = aspace_client.get(tc["ref"]).json()
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
        alma_items.extend(current_items.get("item"))
    return alma_items


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
    args = parser.parse_args()

    if args.alma_environment == "sandbox":
        alma_api_key = API_KEYS["SANDBOX"]
    elif args.alma_environment == "production":
        alma_api_key = API_KEYS["DIIT_SCRIPTS"]

    # load profile module
    profile_module = import_module(args.profile)
    match_containers = getattr(profile_module, "match_containers")

    logger.info(f"Using Alma API key for {args.alma_environment} environment")
    alma_client = AlmaAPIClient(alma_api_key)
    alma_items = get_alma_items(alma_client, args.bib_id, args.holdings_id)
    logger.info(f"Found {len(alma_items)} items in Alma")

    logger.info(f"Getting ASpace top containers for resource {args.resource_id}")
    aspace_client = ASnakeClient(config_file=args.asnake_config)
    aspace_containers = get_aspace_containers(aspace_client, args.resource_id)
    logger.info(f"Found {len(aspace_containers)} top containers in ASpace")

    # find top containers with existing barcodes
    # write them to a file and remove them from the list of ASpace containers
    top_containers_with_barcodes = [tc for tc in aspace_containers if tc.get("barcode")]
    if top_containers_with_barcodes:
        logger.info(
            f"Found {len(top_containers_with_barcodes)} top containers with existing barcodes"
        )
        with open("top_containers_with_existing_barcodes.json", "w") as f:
            json.dump(top_containers_with_barcodes, f, indent=2)
        # remove top containers with barcodes from the list
        aspace_containers = [
            tc for tc in aspace_containers if tc not in top_containers_with_barcodes
        ]

    matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers = (
        match_containers(alma_items, aspace_containers, logger)
    )

    # update ASpace top containers with barcodes
    for tc in matched_aspace_containers:
        aspace_client.post(tc["uri"], json=tc)
        logger.info(f"Added barcode to top container {tc['uri']}")

    logger.info(f"Updated barcodes for {len(matched_aspace_containers)} top containers")

    # if there are unmatched items or containers, write them to JSON files
    if unmatched_alma_items:
        logger.info(f"Found {len(unmatched_alma_items)} unmatched Alma items.")
        with open("unmatched_alma_items.json", "w") as f:
            json.dump(unmatched_alma_items, f, indent=2)
        logger.info("Unmatched Alma items written to unmatched_alma_items.json")

    if unmatched_aspace_containers:
        logger.info(
            f"Found {len(unmatched_aspace_containers)} unmatched ASpace top containers."
        )
        with open("unmatched_aspace_containers.json", "w") as f:
            json.dump(unmatched_aspace_containers, f, indent=2)
        logger.info(
            "Unmatched ASpace top containers written to unmatched_aspace_containers.json"
        )
