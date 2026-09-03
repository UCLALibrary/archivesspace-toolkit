import argparse
import csv
from pathlib import Path

from asnake.client import ASnakeClient
import asnake.logging as logging
from dataclasses import dataclass, field

from utils import configure_logging, load_config
from utils.aspace_utils import get_resource_by_uri, update_external_ids
from utils.generic_utils import write_dicts_to_csv

# Logger available globally within this module.
# Configuration is done by configure_logging(), called in main().
logger = logging.get_logger(Path(__file__).stem)

# ASpace external_id sources this script manages. Any other source on a
# resource's external_ids is left untouched.
ILS_SOURCE = "ILS"
OCLC_SOURCE = "OCLC"
LEGACY_AT_SOURCE = "Archivists Toolkit Database::RESOURCE"

# Expected column headers in the input CSV (as_oclc_mms.csv).
CSV_NAME_COL = "Name"
CSV_URI_COL = "ASpace URI"
CSV_MMS_ID_COL = "MMS ID"
CSV_OCLC_COL = "OCLC ID"


@dataclass
class RowResult:
    error_message: str | None
    changes: list[dict[str, str | None]] = field(default_factory=list)
    row_summary: dict[str, str | None] = field(default_factory=dict)


# CLI


def _get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import OCLC and MMS External IDs into ArchivesSpace resources, "
            "and remove legacy Archivists Toolkit External IDs from the same "
            "resources."
        )
    )
    parser.add_argument(
        "--input_csv",
        required=True,
        help=(
            f"Path to input CSV (as_oclc_mms.csv) with columns "
            f"'{CSV_NAME_COL}', '{CSV_URI_COL}', '{CSV_MMS_ID_COL}', '{CSV_OCLC_COL}' "
            "(other columns are ignored)"
        ),
    )
    parser.add_argument("--config_file", required=True, help="Path to config YAML")
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Do not write any changes to ArchivesSpace",
    )
    parser.add_argument(
        "--print_output",
        action="store_true",
        help="Print output to console in addition to log file",
    )
    return parser.parse_args()


# DATA RETRIEVAL


def _read_input_rows(input_csv: str) -> list[dict]:
    """Read and lightly validate rows from the input CSV.

    :param str input_csv: Path to the input CSV file.
    :return: A list of row dicts.
    :raises ValueError: If the CSV is missing any expected columns.
    """
    with open(input_csv, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        expected = {CSV_NAME_COL, CSV_URI_COL, CSV_MMS_ID_COL, CSV_OCLC_COL}
        missing = expected - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Input CSV is missing expected column(s): {missing}. "
                f"Found columns: {reader.fieldnames}"
            )
        return list(reader)


# PROCESSING


def _process_row(aspace_client: ASnakeClient, row: dict, dry_run: bool) -> RowResult:
    """Process a single input row: fetch the resource by its ASpace URI, apply
    External ID changes, and (unless dry_run) write them back to ArchivesSpace.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param dict row: A single row dict from the input CSV.
    :param bool dry_run: If True, do not write changes to ArchivesSpace.
    :return: RowResult object containing any changes made and/or error messages.
    """
    name = row.get(CSV_NAME_COL, "").strip()
    uri = row.get(CSV_URI_COL, "").strip()
    mms_id = row.get(CSV_MMS_ID_COL, "").strip()
    oclc_number = row.get(CSV_OCLC_COL, "").strip()

    # Use the URI as the identifying label in messages when Name is blank,
    # since URI is the one field we can't proceed without.
    identifier = name or uri

    row_summary = {
        "name": name,
        "resource_uri": uri,
        "mms_id": mms_id,
        "oclc_id": oclc_number,
    }

    if not uri:
        return RowResult(
            error_message=f"Row '{identifier}' has no ASpace URI; skipping",
            changes=[],
            row_summary=row_summary,
        )

    resource = get_resource_by_uri(aspace_client, uri)
    if resource is None:
        return RowResult(
            error_message=f"Could not fetch ASpace resource at '{uri}' (row '{identifier}')",
            changes=[],
            row_summary=row_summary,
        )
    ids_to_set = {}
    if mms_id:
        ids_to_set[ILS_SOURCE] = mms_id
    else:
        logger.warning(
            f"No MMS ID provided for resource '{identifier}'; leaving ILS External ID as-is"
        )
    if oclc_number:
        ids_to_set[OCLC_SOURCE] = oclc_number
    else:
        logger.warning(
            f"No OCLC number provided for resource '{identifier}'; leaving OCLC External ID as-is"
        )

    changes = update_external_ids(
        resource, ids_to_set, sources_to_remove={LEGACY_AT_SOURCE}
    )

    if not changes:
        logger.info(
            f"No External ID changes needed for '{identifier}' ({resource['uri']})"
        )
        return RowResult(
            error_message=None,
            changes=[],
            row_summary=row_summary,
        )

    for change in changes:
        logger.info(
            f"'{identifier}' ({resource['uri']}): {change['action']} "
            f"External ID [{change['source']}]: {change['before']!r} -> {change['after']!r}"
        )

    if dry_run:
        return RowResult(
            error_message=None,
            changes=changes,
            row_summary=row_summary,
        )

    response = aspace_client.post(resource["uri"], json=resource)
    if response.status_code == 200:
        logger.info(f"Updated resource {resource['uri']}")
        return RowResult(
            error_message=None,
            changes=changes,
            row_summary=row_summary,
        )
    error = f"Failed to update resource {resource['uri']}: {response.status_code} {response.text}"
    logger.error(error)
    return RowResult(
        error_message=error,
        changes=changes,
        row_summary=row_summary,
    )


# REPORTING


def _print_summary(
    all_results: list[RowResult],
    print_output: bool,
) -> None:
    """Log a run summary and optionally print it to the console.

    :param list all_results: List of RowResult objects from processing all rows.
    :param bool print_output: If True, also print the summary to the console.
    """
    all_changes = [c for result in all_results for c in result.changes]
    resources_changed = len({c["resource_uri"] for c in all_changes})
    total_rows = len(all_results)
    unchanged_rows = [
        result.row_summary
        for result in all_results
        if not result.changes and not result.error_message
    ]
    errors = [result.error_message for result in all_results if result.error_message]

    summary_lines = [
        f"Total input rows: {total_rows}",
        f"Resources with External ID changes: {resources_changed}",
        f"Total External ID changes (added/updated/removed): {len(all_changes)}",
        f"Resources already up to date (no changes needed): {len(unchanged_rows)}",
        f"Errors/skipped rows: {len(errors)}",
    ]
    for line in summary_lines:
        logger.info(line)
        if print_output:
            print(line)


def _write_result_csvs(
    all_results: list[RowResult],
    logging_filename_base: str,
) -> None:
    """Write CSV reports for changes, unchanged rows, and errors.

    :param list all_results: List of RowResult objects from processing all rows.
    :param str logging_filename_base: Base name for the log file (used to name CSVs).
    """
    all_changes = [c for result in all_results for c in result.changes]
    if all_changes:
        report_path = write_dicts_to_csv(
            f"changes_{logging_filename_base}.csv", all_changes
        )
        logger.info(f"Change report written to {report_path}")

    unchanged_rows = [
        result.row_summary
        for result in all_results
        if not result.changes and not result.error_message
    ]
    if unchanged_rows:
        unchanged_path = write_dicts_to_csv(
            f"no_updates_needed_{logging_filename_base}.csv", unchanged_rows
        )
        logger.info(f"No-updates-needed report written to {unchanged_path}")

    errors = [result.error_message for result in all_results if result.error_message]
    if errors:
        error_rows = [{"error": e} for e in errors]
        error_path = write_dicts_to_csv(
            f"errors_{logging_filename_base}.csv", error_rows
        )
        logger.info(f"Error report written to {error_path}")


# MAIN


def main() -> None:
    """Import OCLC/MMS External IDs into ArchivesSpace resources, removing
    legacy Archivists Toolkit External IDs from the same resources."""
    logging_filename_base = Path(__file__).stem
    print(f"Logging to {logging_filename_base}.log")

    args = _get_args()
    configure_logging(log_filename_stem=logging_filename_base, dry_run=args.dry_run)

    config = load_config(args.config_file)
    aspace_client = ASnakeClient(**config)

    rows = _read_input_rows(args.input_csv)
    logger.info(f"Read {len(rows)} rows from {args.input_csv}")

    all_results: list[RowResult] = []

    for row in rows:
        result = _process_row(aspace_client, row, args.dry_run)
        all_results.append(result)

    if args.dry_run:
        logger.info(
            f"Dry run: no changes written. Would have made "
            f"{sum(len(result.changes) for result in all_results)} External ID changes across "
            f"{len({c['resource_uri'] for result in all_results for c in result.changes})} "
            f"resources."
        )

    _print_summary(all_results, args.print_output)
    _write_result_csvs(all_results, logging_filename_base)


if __name__ == "__main__":
    main()
