import argparse
import json
from pathlib import Path
from alma_api_keys import API_KEYS
from alma_api_client import AlmaAPIClient

# from asnake.client import ASnakeClient


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


def get_alma_items(
    alma_client: AlmaAPIClient, bib_id: str, holdings_id: str
) -> list[dict]:
    # EXPERIMENT: Get data from file if it exists
    alma_data_file = Path(f"alma_data_{holdings_id}.json")

    if alma_data_file.exists():
        print(f"Reading alma data from {alma_data_file}")
        with open(alma_data_file, "r") as f:
            alma_items = json.load(f)
    else:
        alma_items = _get_alma_items_from_alma(alma_client, bib_id, holdings_id)
        # EXPERIMENT: Store data in file for possible later use.
        print(f"Writing alma data to {alma_data_file}")
        with open(alma_data_file, "w") as f:
            json.dump(alma_items, f)
    return alma_items


def main() -> None:
    args: argparse.Namespace = _get_args()
    alma_client = AlmaAPIClient(_get_alma_api_key(args.alma_environment))
    alma_items = get_alma_items(alma_client, args.bib_id, args.holdings_id)
    print(f"Found {len(alma_items)} items in Alma")

    # aspace_client = ASnakeClient(config_file=args.asnake_config)


if __name__ == "__main__":
    main()
