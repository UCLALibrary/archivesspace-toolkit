import argparse
from datetime import datetime

from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient
from pathlib import Path

from utils import load_config, write_dicts_to_csv
from utils.alma_utils import get_alma_items_from_alma
from utils.aspace_utils import get_container_refs_from_db

from config.base_match import match_containers
from config.indicator_type_matching import get_aspace_match_data, get_alma_match_data


def _get_args() -> argparse.Namespace:
    """Get command-line arguments for this program."""
    parser = argparse.ArgumentParser(
        description=(
            "Using ArchivesSpace and Alma IDs for the same LSC collection, "
            "list Alma items whose box identifier has no matching top container in ArchivesSpace."
        )
    )
    parser.add_argument(
        "-c",
        "--config_file",
        type=str,
        required=True,
        help=(
            "Path to YAML config file with ArchivesSpace credentials, "
            "including database connection settings and Alma API key."
        ),
    )
    parser.add_argument(
        "--repo_id",
        type=int,
        required=False,
        default=2,
        help="ArchivesSpace repository ID. Defaults to 2.",
    )
    parser.add_argument(
        "-r",
        "--resource_id",
        type=int,
        required=True,
        help="ArchivesSpace resource ID (i.e. collection) to process.",
    )
    parser.add_argument(
        "--bib_id",
        type=str,
        required=True,
        help="Alma bib MMS ID for the same collection.",
    )
    parser.add_argument(
        "--holdings_id",
        type=str,
        required=True,
        help="Alma holdings MMS ID for the same collection.",
    )
    parser.add_argument(
        "-o",
        "--output_path",
        type=str,
        required=False,
        default=(
            f"reports/aspace_missing_containers_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            ".csv"
        ),
        help=(
            "Path to write the CSV report. "
            "Defaults to 'reports/aspace_missing_containers_<DATETIME>.csv'."
        ),
    )
    return parser.parse_args()


def _get_all_top_containers_for_resource(
    aspace_client: ASnakeClient,
    db_config: dict,
    resource_id: int,
) -> list[dict]:
    """Fetch all top containers linked to the given resource.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict db_config: DB connection settings.
    :param int resource_id: The ID of the resource to process.
    :return: A list of top container dictionaries.
    """
    container_refs = get_container_refs_from_db(db_config, resource_id)

    all_tcs: list[dict] = []
    for ref in container_refs:
        try:
            tc = aspace_client.get(ref).json()
        except Exception as err:
            print(f"Error fetching top container {ref}: {err}. Skipping.")
            continue
        all_tcs.append(tc)
    return all_tcs


def _get_aspace_resource_info(
    aspace_client: ASnakeClient,
    resource_uri: str,
) -> tuple[str, str]:
    """Get the human-readable identifier and title for the given resource.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str resource_uri: The URI of the resource to process.
    :return tuple[str, str]: A tuple of the human-readable identifier and the title of the resource.
    """
    resource = aspace_client.get(resource_uri).json()
    # ArchiveSpace resource object has 4 identifier fields, `id_0` to `id_3`.
    # Join all available parts to get human-readable identifier.
    human_readable_id = "-".join(
        [resource.get(f"id_{i}", "") for i in range(4) if resource.get(f"id_{i}", "")]
    )
    return human_readable_id, resource.get("title", "")


def _prepare_report_rows(
    unmatched_alma_items: list[dict],
    alma_bib_id: str,
    aspace_resource_uri: str,
    aspace_resource_id_human_readable: str,
    aspace_resource_title: str,
) -> list[dict]:
    """Prepare CSV row dicts for unmatched Alma items.

    :param list[dict] unmatched_alma_items: A list of Alma item dicts.
    :param str alma_bib_id: The Alma bib ID.
    :param str aspace_resource_uri: The URI of the ASpace resource.
    :param str aspace_resource_id_human_readable: The human readable identifier
    of the ASpace resource (e.g. "LSC--0293").
    :param str aspace_resource_title: The title of the ASpace resource.
    :return list[dict]: A list of CSV row dictionaries.
    """
    rows: list[dict] = []
    for alma_item in unmatched_alma_items:
        rows.append(
            {
                "ASpace Resource URI": aspace_resource_uri,
                "ASpace Resource Identifier": aspace_resource_id_human_readable,
                "ASpace Collection Title": aspace_resource_title,
                "Alma Bib ID": alma_bib_id,
                "Alma Item Barcode": alma_item.get("barcode", ""),
                "Alma Box Identifier": alma_item.get("description", ""),
                "ASpace Match Found": "No",  # this is always "No" for unmatched items
                "Notes": "No matching ASpace top container found — requires review",
            }
        )
    return rows


def main() -> None:
    """For a given LSC collection, produce a CSV report of boxes cataloged in Alma
    that have no matching ArchivesSpace top container. Collections are identified by
    ASpace resource ID and Alma bib and holdings IDs.

    Summary of LSC ticket:
    1. Retrieve all top containers from ArchivesSpace for the given collection,
        and all Item records from Alma for the same collection.
    2. Normalize the box identifiers to use the same format in each set.
    3. Compare the two normalized sets and generate a CSV report with the following info:
        - Alma item identifier / barcode
        - Alma box identifier as it appears in the item record
        - Collection identifier (ASpace resource ID)
        - A status note: "No matching ASpace top container found — requires review"
    """
    args = _get_args()
    config = load_config(args.config_file)

    aspace_client = ASnakeClient(**config)
    db_config = config.get("database")
    if not db_config:
        raise ValueError("DB connection settings are required.")

    print(
        f"Running container comparison report for "
        f"ASpace resource ID: {args.resource_id}, "
        f"Alma Bib ID: {args.bib_id}, "
        f"Alma Holdings ID: {args.holdings_id}"
    )

    # Get all top containers for the given collection from ASpace
    aspace_top_containers = _get_all_top_containers_for_resource(
        aspace_client, db_config, args.resource_id
    )
    print(
        f"Fetched {len(aspace_top_containers)} "
        f"top container{'s' if len(aspace_top_containers) > 1 else ''} from ASpace"
    )

    # For use in report
    aspace_resource_uri = f"/repositories/{args.repo_id}/resources/{args.resource_id}"
    aspace_resource_id_human_readable, aspace_resource_title = (
        _get_aspace_resource_info(aspace_client, aspace_resource_uri)
    )
    # Reuse the `indicator_type_matching` logic to create aspace match data dict
    aspace_match_data, _ = get_aspace_match_data(aspace_top_containers)

    # Now get all items for the given collection from Alma
    alma_api_key = config["alma_config"]["alma_api_key"]
    alma_client = AlmaAPIClient(alma_api_key)
    alma_items = get_alma_items_from_alma(alma_client, args.bib_id, args.holdings_id)
    print(
        f"Fetched {len(alma_items)} "
        f"item{'s' if len(alma_items) > 1 else ''} from Alma"
    )
    # And reuse the `indicator_type_matching` logic again
    alma_match_data, _ = get_alma_match_data(alma_items)

    # Use the unmatched data to prepare the report rows
    _, unmatched_data = match_containers(alma_match_data, aspace_match_data)
    unmatched_alma_items = unmatched_data.get("unmatched_alma_items", [])
    if not unmatched_alma_items:
        print("No unmatched Alma items found. Exiting.")
        return
    print(
        f"Found {len(unmatched_alma_items)} "
        f"Alma item{'s' if len(unmatched_alma_items) > 1 else ''} "
        "without a matching ASpace top container"
    )

    # Write the CSV report
    print(f"Writing CSV report to {args.output_path}")
    rows = _prepare_report_rows(
        unmatched_alma_items,
        args.bib_id,
        aspace_resource_uri,
        aspace_resource_id_human_readable,
        aspace_resource_title,
    )
    output_path = Path(args.output_path)
    write_dicts_to_csv(output_path, rows)


if __name__ == "__main__":
    main()
