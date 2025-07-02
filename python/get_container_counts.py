from asnake.client import ASnakeClient
from add_alma_barcodes_to_archivesspace import _get_container_refs_from_db
import argparse
import csv
from pathlib import Path


def main(args: argparse.Namespace) -> None:
    """Counts the number of containers associated with a resource,
    for each resource identified in the LSC Airtable data,
    then appends the count to the Airtable data
    and saves the extended data to a CSV file.

    :param args: CLI arguments for this script.
    """
    client = ASnakeClient(config_file=args.config_file)

    data_with_counts = []
    with open(args.file_name, "r", newline="", encoding="utf-8") as file:
        lsc_data = csv.DictReader(file)

        print("Counting containers for each resource ID...")
        for row in lsc_data:
            if row["ArchivesSpace Rec ID"]:  # only get count for rows with Rec ID
                try:
                    resource_id = int(row["ArchivesSpace Rec ID"])
                    # Counting container refs, rather than getting all container data
                    db_settings = client.config.get("database")
                    container_refs = (
                        _get_container_refs_from_db(  # imported from existing script
                            db_settings, resource_id
                        )
                    )
                    row["container_count"] = len(container_refs)
                except ValueError:
                    print(
                        f"{row["Identifier"]} does not have a valid Rec ID. Skipping..."
                    )
                    continue
            else:  # set empty string for rows without Rec ID
                row["container_count"] = ""

            data_with_counts.append(row)

    output_filename = Path(args.file_name).stem + "_with_container_counts.csv"
    print(f"Done! Writing counts to {output_filename}...")
    with open(output_filename, "w+", newline="", encoding="utf-8") as output_file:
        fieldnames = data_with_counts[0].keys()
        writer = csv.DictWriter(output_file, fieldnames)
        writer.writeheader()
        writer.writerows(data_with_counts)


if __name__ == "__main__":
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
    args = parser.parse_args()

    main(args)
