import argparse
import csv
from pathlib import Path

from asnake.client import ASnakeClient
import asnake.logging as logging

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


def _process_row(
    aspace_client: ASnakeClient, row: dict, dry_run: bool
) -> tuple[list[dict], str | None, dict]:
    """Process a single input row: fetch the resource by its ASpace URI, apply
    External ID changes, and (unless dry_run) write them back to ArchivesSpace.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param dict row: A single row dict from the input CSV.
    :param bool dry_run: If True, do not write changes to ArchivesSpace.
    :return: Tuple of (list of change record dicts, error message or None,
        row summary dict). An empty change list with no error means the
        resource needed no changes. The row summary is always populated (even
        on error/skip) so callers can report on it either way.
    """
    name = (row.get(CSV_NAME_COL) or "").strip()
    uri = (row.get(CSV_URI_COL) or "").strip()
    mms_id = (row.get(CSV_MMS_ID_COL) or "").strip()
    oclc_number = (row.get(CSV_OCLC_COL) or "").strip()

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
        return [], f"Row '{identifier}' has no ASpace URI; skipping", row_summary

    resource = get_resource_by_uri(aspace_client, uri)
    if resource is None:
        return (
            [],
            f"Could not fetch ASpace resource at '{uri}' (row '{identifier}')",
            row_summary,
        )

    ids_to_set = {}
    if mms_id:
        ids_to_set[ILS_SOURCE] = mms_id
    else:
        logger.warning(
            f"No MMS ID for resource '{identifier}'; leaving ILS External ID as-is"
        )
    if oclc_number:
        ids_to_set[OCLC_SOURCE] = oclc_number
    else:
        logger.warning(
            f"No OCLC number for resource '{identifier}'; leaving OCLC External ID as-is"
        )

    changes = update_external_ids(
        resource, ids_to_set, sources_to_remove={LEGACY_AT_SOURCE}
    )

    if not changes:
        logger.info(
            f"No External ID changes needed for '{identifier}' ({resource['uri']})"
        )
        return [], None, row_summary

    for change in changes:
        logger.info(
            f"'{identifier}' ({resource['uri']}): {change['action']} "
            f"External ID [{change['source']}]: {change['before']!r} -> {change['after']!r}"
        )
        change["resource_identifier"] = identifier
        change["resource_uri"] = resource["uri"]

    if dry_run:
        return changes, None, row_summary

    response = aspace_client.post(resource["uri"], json=resource)
    if response.status_code == 200:
        logger.info(f"Updated resource {resource['uri']}")
        return changes, None, row_summary
    error = f"Failed to update resource {resource['uri']}: {response.status_code} {response.text}"
    logger.error(error)
    return changes, error, row_summary


# REPORTING


def _print_summary(
    total_rows: int,
    changes: list[dict],
    unchanged_rows: list[dict],
    errors: list[str],
    print_output: bool,
) -> None:
    """Log a run summary and optionally print it to the console.

    :param int total_rows: Total input rows processed.
    :param list[dict] changes: All change records written (or that would be
        written, in a dry run).
    :param list[dict] unchanged_rows: Row summaries for resources that needed
        no changes (already up to date).
    :param list[str] errors: Error/skip messages encountered.
    :param bool print_output: If True, also print to console.
    """
    resources_changed = len({c["resource_uri"] for c in changes})
    summary_lines = [
        f"Total input rows: {total_rows}",
        f"Resources with External ID changes: {resources_changed}",
        f"Total External ID changes (added/updated/removed): {len(changes)}",
        f"Resources already up to date (no changes needed): {len(unchanged_rows)}",
        f"Errors/skipped rows: {len(errors)}",
    ]
    for line in summary_lines:
        logger.info(line)
        if print_output:
            print(line)


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

    all_changes: list[dict] = []
    unchanged_rows: list[dict] = []
    errors: list[str] = []

    for row in rows:
        changes, error, row_summary = _process_row(aspace_client, row, args.dry_run)
        all_changes.extend(changes)
        if error:
            errors.append(error)
            logger.error(error)
        elif not changes:
            unchanged_rows.append(row_summary)

    if args.dry_run:
        logger.info(
            f"Dry run: no changes written. Would have made {len(all_changes)} "
            f"External ID changes across "
            f"{len({c['resource_uri'] for c in all_changes})} resources."
        )

    _print_summary(len(rows), all_changes, unchanged_rows, errors, args.print_output)

    if all_changes:
        report_path = write_dicts_to_csv(
            f"changes_{logging_filename_base}.csv", all_changes
        )
        logger.info(f"Change report written to {report_path}")

    if unchanged_rows:
        unchanged_path = write_dicts_to_csv(
            f"no_updates_needed_{logging_filename_base}.csv", unchanged_rows
        )
        logger.info(f"No-updates-needed report written to {unchanged_path}")

    if errors:
        error_rows = [{"error": e} for e in errors]
        error_path = write_dicts_to_csv(
            f"errors_{logging_filename_base}.csv", error_rows
        )
        logger.info(f"Error report written to {error_path}")


if __name__ == "__main__":
    main()
