import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from asnake.client import ASnakeClient
import asnake.logging as logging

from utils import configure_logging, load_config, write_dicts_to_csv
from utils.aspace_utils import (
    get_ao_uris_by_external_id_source_from_db,
    get_required_db_settings,
    update_external_ids,
)

# Logger available globally within this module.
# Configuration is done by configure_logging(), called in main().
logger = logging.get_logger(Path(__file__).stem)

# Any External ID whose source starts with this string is removed.
AT_SOURCE_PREFIX = "Archivists Toolkit"

# Default to UCLA's main AS repository ID (2).
DEFAULT_REPO_ID = 2

# How often (in archival objects checked) to log progress.
PROGRESS_INTERVAL = 1000


@dataclass
class AOResult:
    """Outcome of processing one candidate archival object."""

    ao_uri: str
    changes: list[dict] = field(default_factory=list)
    other_ids_kept: int = 0
    error_message: str | None = None


# CLI


def _get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Remove External IDs whose source starts with "
            f"'{AT_SOURCE_PREFIX}' from all archival objects in a repository. "
            "All other External IDs are left untouched."
        )
    )
    parser.add_argument("--config_file", required=True, help="Path to config YAML")
    parser.add_argument(
        "--repo_id",
        type=int,
        default=DEFAULT_REPO_ID,
        help=(
            "Numeric ID of the ASpace repository to process "
            f"(default: {DEFAULT_REPO_ID})."
        ),
    )
    parser.add_argument(
        "--use_db",
        action="store_true",
        help=(
            "Find archival objects with AT External IDs via the database instead "
            "of paging through every archival object via the API. Records are "
            "still read and updated via the API."
        ),
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Report what would be removed without writing to ArchivesSpace",
    )
    parser.add_argument(
        "--print_output",
        action="store_true",
        help="Print progress and summary to console in addition to log file",
    )
    return parser.parse_args()


# MATCHING


def _is_AT_source(source: str | None) -> bool:
    """Return True if an External ID source is an Archivists' Toolkit source.

    :param str | None source: The `source` value of an external_id subrecord.
    :return: True if the source starts with AT_SOURCE_PREFIX.
    """
    return isinstance(source, str) and source.startswith(AT_SOURCE_PREFIX)


def _get_AT_sources(record: dict) -> set[str]:
    """Return the distinct AT source strings present on a record's External IDs.

    :param dict record: An ASpace record dict (e.g. an archival object).
    :return: Set of matching source strings; empty if the record has none.
    """
    return {
        eid["source"]
        for eid in record.get("external_ids", [])
        if _is_AT_source(eid.get("source"))
    }


def _remove_AT_external_ids(record: dict) -> tuple[list[dict], int]:
    """Remove AT External IDs from a record in place.

    Uses the shared update_external_ids() helper with nothing to add, so only
    entries with an AT source are dropped and every other entry is kept as-is.

    :param dict record: An ASpace record dict; mutated in place.
    :return: Tuple of (report rows for each removed ID, count of other
        External IDs kept on the record).
    """
    at_sources = _get_AT_sources(record)
    if not at_sources:
        return [], len(record.get("external_ids", []))

    changes = update_external_ids(record, ids_to_add=[], sources_to_remove=at_sources)

    resource_ref = (record.get("resource") or {}).get("ref")
    rows = [
        {
            "ao_uri": record.get("uri"),
            "ao_ref_id": record.get("ref_id"),
            "ao_title": record.get("title") or record.get("display_string"),
            "level": record.get("level"),
            "resource_uri": resource_ref,
            "source": change["source"],
            "removed_value": change["before"],
        }
        for change in changes
        if change["action"] == "removed"
    ]
    return rows, len(record.get("external_ids", []))


# PROCESSING


def _process_archival_object(
    aspace_client: ASnakeClient,
    uri: str,
    dry_run: bool,
    listed_ao: dict | None = None,
) -> AOResult:
    """Remove AT External IDs from one archival object.

    The record is fetched from the API unless this is a dry run and a record
    from the paged API listing was supplied.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param str uri: The archival object's URI.
    :param bool dry_run: If True, do not write changes to ArchivesSpace.
    :param dict | None listed_ao: Archival object dict from the paged API
        listing, if available (None when candidates come from the database).
    :return: AOResult describing what was (or would be) removed.
    """
    if dry_run and listed_ao is not None:
        ao = listed_ao
    else:
        response = aspace_client.get(uri)
        if response.status_code != 200:
            error = f"Could not fetch {uri}: {response.status_code} {response.text}"
            logger.error(error)
            return AOResult(ao_uri=uri, error_message=error)
        ao = response.json()

    rows, kept = _remove_AT_external_ids(ao)

    if dry_run:
        return AOResult(ao_uri=uri, changes=rows, other_ids_kept=kept)

    response = aspace_client.post(uri, json=ao)
    if response.status_code != 200:
        error = f"Failed to update {uri}: {response.status_code} {response.text}"
        logger.error(error)
        return AOResult(
            ao_uri=uri, changes=rows, other_ids_kept=kept, error_message=error
        )

    return AOResult(ao_uri=uri, changes=rows, other_ids_kept=kept)


def _log_progress(message: str, print_output: bool) -> None:
    """Log a progress message and optionally print it to the console."""
    logger.info(message)
    if print_output:
        print(message)


def _process_repository(
    aspace_client: ASnakeClient,
    repo_id: int,
    dry_run: bool,
    print_output: bool,
    db_settings: dict,
    use_db: bool,
) -> tuple[int, list[AOResult]]:
    """Remove Archivists' Toolkit (AT) External IDs from every archival object in a repository.

    Without use_db, pages through every archival object via the API and
    processes those with AT External IDs. Removing External IDs does not change
    which archival objects exist or their order, so editing records while paging
    is safe. With use_db, gets the candidate archival objects from the
    database and processes only those.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param int repo_id: Numeric ASpace repository ID.
    :param bool dry_run: If True, do not write changes to ArchivesSpace.
    :param bool print_output: If True, also print progress to the console.
    :param dict db_settings: DB connection details.
    :param bool use_db: If True, get candidates from the database.
    :return: Tuple of (archival objects checked, results for processed AOs).
        "Checked" means every AO in the repository (API) or every database
        candidate (database).
    """
    checked = 0
    results: list[AOResult] = []

    if use_db:
        uris = get_ao_uris_by_external_id_source_from_db(
            db_settings, repo_id, AT_SOURCE_PREFIX
        )
        _log_progress(
            f"Repository {repo_id}: database found {len(uris)} archival objects "
            f"with External ID sources starting with '{AT_SOURCE_PREFIX}'",
            print_output,
        )
        candidates = ((uri, None) for uri in uris)
    else:
        candidates = (
            (ao["uri"], ao)
            for ao in aspace_client.get_paged(
                f"repositories/{repo_id}/archival_objects"
            )
        )

    for uri, listed_ao in candidates:
        checked += 1
        if checked % PROGRESS_INTERVAL == 0:
            _log_progress(
                f"Repository {repo_id}: checked {checked} archival objects, "
                f"{sum(1 for r in results if r.changes)} with AT External IDs so far",
                print_output,
            )

        # With the API listing, skip records without AT IDs before any extra call.
        if listed_ao is not None and not _get_AT_sources(listed_ao):
            continue

        result = _process_archival_object(aspace_client, uri, dry_run, listed_ao)
        for row in result.changes:
            verb = "Would remove" if dry_run else "Removed"
            logger.info(
                f"{verb} External ID [{row['source']}] {row['removed_value']!r} "
                f"from {row['ao_uri']}"
            )
        results.append(result)

    return checked, results


# REPORTING


def _print_summary(
    repo_id: int,
    checked: int,
    results: list[AOResult],
    dry_run: bool,
    use_db: bool,
    print_output: bool,
) -> None:
    """Log a run summary and optionally print it to the console.

    :param int repo_id: The repository processed.
    :param int checked: Archival objects checked (scanned or database candidates).
    :param list results: AOResult objects for every processed archival object.
    :param bool dry_run: Whether this was a dry run (changes the wording).
    :param bool use_db: Whether candidates came from the database.
    :param bool print_output: If True, also print the summary to the console.
    """
    all_changes = [row for r in results for row in r.changes]
    with_at_ids = [r for r in results if r.changes]
    succeeded = [r for r in with_at_ids if not r.error_message]
    errors = [r for r in results if r.error_message]
    nothing_to_remove = [r for r in results if not r.changes and not r.error_message]
    checked_label = (
        "Database candidates checked" if use_db else "Archival objects scanned"
    )
    source_counts = Counter(row["source"] for row in all_changes)
    verb = "would be removed" if dry_run else "removed"

    lines = [
        "DRY RUN: no changes written" if dry_run else "LIVE RUN",
        f"Candidates from: {'database' if use_db else 'API'}",
        f"Repository: {repo_id}",
        f"{checked_label}: {checked}",
        f"Archival objects with AT External IDs: {len(with_at_ids)}",
        f"Fetched records with nothing to remove: {len(nothing_to_remove)}",
        f"Archival objects updated{' (would be)' if dry_run else ''}: {len(succeeded)}",
        f"AT External IDs {verb}: {len(all_changes)}",
        (
            "Other External IDs kept on those archival objects: "
            f"{sum(r.other_ids_kept for r in with_at_ids)}"
        ),
        f"Errors: {len(errors)}",
        "Distinct AT sources matched (source: count):",
    ]
    lines += [f"  {source}: {count}" for source, count in source_counts.most_common()]

    for line in lines:
        logger.info(line)
        if print_output:
            print(line)


def _write_result_csvs(results: list[AOResult], filename_base: str) -> None:
    """Write CSV reports of removed (or to-be-removed) IDs and of errors.

    :param list results: AOResult objects for every processed archival object.
    :param str filename_base: Base name used to name the CSVs.
    """
    all_changes = [row for r in results for row in r.changes]
    if all_changes:
        path = write_dicts_to_csv(f"changes_{filename_base}.csv", all_changes)
        logger.info(f"Change report written to {path}")

    error_rows = [
        {"ao_uri": r.ao_uri, "error": r.error_message}
        for r in results
        if r.error_message
    ]
    if error_rows:
        path = write_dicts_to_csv(f"errors_{filename_base}.csv", error_rows)
        logger.info(f"Error report written to {path}")


# MAIN


def main() -> None:
    """Remove Archivists' Toolkit External IDs from a repository's archival objects."""
    filename_base = Path(__file__).stem
    args = _get_args()
    log_name = configure_logging(log_filename_stem=filename_base, dry_run=args.dry_run)
    print(f"Logging to {log_name}")

    config = load_config(args.config_file)
    # Database settings are required for every run, matching other toolkit scripts.
    db_settings = get_required_db_settings(config)
    aspace_client = ASnakeClient(**config)

    response = aspace_client.get(f"repositories/{args.repo_id}")
    if response.status_code != 200:
        message = (
            f"Could not fetch repository {args.repo_id}: "
            f"{response.status_code} {response.text}"
        )
        logger.error(message)
        sys.exit(message)
    repo = response.json()
    message = (
        f"Processing repository {args.repo_id}: "
        f"{repo.get('repo_code')} ({repo.get('name')})"
    )
    logger.info(message)
    if args.print_output:
        print(message)

    checked, results = _process_repository(
        aspace_client,
        args.repo_id,
        args.dry_run,
        args.print_output,
        db_settings,
        args.use_db,
    )

    _print_summary(
        args.repo_id, checked, results, args.dry_run, args.use_db, args.print_output
    )
    _write_result_csvs(results, filename_base)


if __name__ == "__main__":
    main()
