from asnake.client import ASnakeClient
import argparse
import csv
from pathlib import Path

from utils import write_dicts_to_csv
from utils.aspace_utils import get_container_refs_from_db


def _get_args() -> argparse.Namespace:
    """Returns the command-line arguments for this program.

    :return argparse.Namespace: The parsed CLI arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file_name",
        help="CSV export of LSC Airtable data with collection identifiers.",
        required=True,
        type=str,
    )
    parser.add_argument(
        "--config_file",
        help="Path to config file with ASpace credentials",
        required=True,
    )

    return parser.parse_args()


def main() -> None:
    """Counts the number of containers associated with a resource,
    for each resource identified in the LSC Airtable data,
    then appends the count to the Airtable data
    and saves the extended data to a CSV file.
    """
    args = _get_args()
    client = ASnakeClient(config_file=args.config_file)
    db_settings = client.config.get("database")

    data_with_counts = []
    with open(args.file_name, "r", newline="", encoding="utf-8") as file:
        lsc_data = csv.DictReader(file)
        total_rows = 0  # since `lsc_data` is a iterator, can't just get len()
        rows_updated_with_counts = 0

        print("Counting containers for each resource ID...")
        for row in lsc_data:
            if row["ArchivesSpace Rec ID"]:  # only get count for rows with Rec ID
                try:
                    resource_id = int(row["ArchivesSpace Rec ID"])
                    # Counting container refs, rather than getting all container data
                    container_refs = get_container_refs_from_db(
                        db_settings, resource_id
                    )
                    row["container_count"] = len(container_refs)
                    rows_updated_with_counts += 1
                except ValueError:
                    print(
                        f'{row["Identifier"]} does not have a valid Rec ID. Skipping...'
                    )
                    continue
            else:  # set empty string for rows without Rec ID
                row["container_count"] = ""

            total_rows += 1
            data_with_counts.append(row)
    print(f"{rows_updated_with_counts} of {total_rows} rows were updated with counts.")

    output_filename = Path(args.file_name).stem + "_with_container_counts.csv"
    print(f"Writing counts to {output_filename}...")
    write_dicts_to_csv(Path(output_filename), data_with_counts)


if __name__ == "__main__":
    main()
