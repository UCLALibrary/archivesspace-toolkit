import argparse
import csv
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from asnake.client import ASnakeClient
import asnake.logging as logging

from utils import configure_logging, load_config
from utils.aspace_utils import (
    get_ao_external_ids_by_source_from_db,
    get_ao_uris_by_external_id_source_from_db,
    get_required_db_settings,
    update_external_ids,
)
from utils.generic_utils import resolve_output_path

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

    ao_uri: str
    changes: list[dict] = field(default_factory=list)
    other_ids_kept: int = 0
    error_message: str | None = None


@dataclass
class RunStats:

    checked: int = 0
    with_AT_ids: int = 0
    updated: int = 0
    nothing_to_remove: int = 0
    errors: int = 0
    removed: int = 0
    other_ids_kept: int | None = 0
    source_counts: Counter = field(default_factory=Counter)
    interrupted: bool = False


class ReportWriter:
    """Writes report rows to CSVs as they are produced, flushing after each row,
    so an interrupted run keeps a record of everything it changed. Files are only
    created when there is something to write.
    """

    def __init__(self, filename_base: str):
        self._filename_base = filename_base
        self._files: dict[str, TextIO] = {}
        self._writers: dict[str, csv.DictWriter] = {}
        self.paths: dict[str, Path] = {}

    def write(self, kind: str, row: dict) -> None:
        """Append a row to the `kind` report (e.g. "changes" or "errors")."""
        if kind not in self._writers:
            path = resolve_output_path(f"{kind}_{self._filename_base}.csv")
            f = open(path, "w", newline="", encoding="utf-8")
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            writer.writeheader()
            self._files[kind], self._writers[kind], self.paths[kind] = f, writer, path
        self._writers[kind].writerow(row)
        self._files[kind].flush()

    def close(self) -> None:
        for f in self._files.values():
            f.close()

    def __enter__(self) -> "ReportWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


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
            "of paging through every archival object via the API. With --dry_run, "
            "the report comes entirely from the database. In a live run, records "
            "are still read and updated via the API."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Process at most this many archival objects with AT External IDs, "
            "then stop. Useful for a small test run, or for working through the "
            "repository in batches."
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
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


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

    # record.get("resource") should always exist,
    # but use (record.get("resource") or {}) to avoid KeyError if it's missing.
    resource_ref = record.get("resource", {}).get("ref")
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
    from the paged API listing was supplied. Records with nothing to remove are
    not posted back, so re-running after an interruption costs only a fetch for
    any record already done.

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
    if not rows:
        # Either removed since the candidate list was made, or the database
        # matched a source that differs only in case.
        logger.info(f"{uri}: no AT External IDs on fetched record; skipping")
        return AOResult(ao_uri=uri, other_ids_kept=kept)

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


def _log_progress_if_due(stats: RunStats, repo_id: int, print_output: bool) -> None:
    """Log a progress line every PROGRESS_INTERVAL archival objects checked."""
    if stats.checked % PROGRESS_INTERVAL == 0:
        _log_progress(
            f"Repository {repo_id}: checked {stats.checked} archival objects, "
            f"{stats.with_AT_ids} with AT External IDs so far",
            print_output,
        )


def _record_result(
    result: AOResult, dry_run: bool, stats: RunStats, writer: ReportWriter
) -> None:
    """Add one result to the running totals and the CSV reports.

    :param AOResult result: Result for one archival object.
    :param bool dry_run: Whether this is a dry run (changes the log wording).
    :param RunStats stats: Running totals; updated in place.
    :param ReportWriter writer: Destination for report rows.
    """
    verb = "Would remove" if dry_run else "Removed"
    for row in result.changes:
        logger.info(
            f"{verb} External ID [{row['source']}] {row['removed_value']!r} "
            f"from {row['ao_uri']}"
        )
        writer.write("changes", row)
        stats.source_counts[row["source"]] += 1

    if result.changes:
        stats.with_AT_ids += 1
        stats.removed += len(result.changes)
        if stats.other_ids_kept is not None:
            stats.other_ids_kept += result.other_ids_kept
        if not result.error_message:
            stats.updated += 1
    if result.error_message:
        stats.errors += 1
        writer.write("errors", {"ao_uri": result.ao_uri, "error": result.error_message})
    elif not result.changes:
        stats.nothing_to_remove += 1


def _iter_dry_run_results_from_db(
    db_settings: dict, repo_id: int, print_output: bool
) -> Iterable[AOResult]:
    """Yield one dry-run AOResult per archival object, built from a single
    database query with no API calls.

    :param dict db_settings: DB connection details.
    :param int repo_id: Numeric ASpace repository ID.
    :param bool print_output: If True, also print progress to the console.
    """
    rows = get_ao_external_ids_by_source_from_db(db_settings, repo_id, AT_SOURCE_PREFIX)

    # Group rows by archival object, keeping database (ID) order.
    rows_by_ao: dict[str, list[dict]] = {}
    for row in rows:
        rows_by_ao.setdefault(row["ao_uri"], []).append(row)
    _log_progress(
        f"Repository {repo_id}: database found {len(rows_by_ao)} archival objects "
        f"with External ID sources starting with '{AT_SOURCE_PREFIX}'",
        print_output,
    )

    for ao_uri, ao_rows in rows_by_ao.items():
        # Re-check case, since SQL LIKE matching is case-insensitive.
        changes = [row for row in ao_rows if _is_AT_source(row["source"])]
        yield AOResult(ao_uri=ao_uri, changes=changes)


def _iter_candidates(
    aspace_client: ASnakeClient,
    repo_id: int,
    db_settings: dict,
    use_db: bool,
    print_output: bool,
) -> Iterable[tuple[str, dict | None]]:
    """Yield (uri, listed record or None) for each archival object to check.

    With use_db, candidates come from the database, which only lists archival
    objects that still have AT External IDs. That makes a live run resumable:
    after an interruption, a new run starts with exactly the remaining records.
    """
    if use_db:
        uris = get_ao_uris_by_external_id_source_from_db(
            db_settings, repo_id, AT_SOURCE_PREFIX
        )
        _log_progress(
            f"Repository {repo_id}: database found {len(uris)} archival objects "
            f"with External ID sources starting with '{AT_SOURCE_PREFIX}'",
            print_output,
        )
        for uri in uris:
            yield uri, None
    else:
        for ao in aspace_client.get_paged(f"repositories/{repo_id}/archival_objects"):
            yield ao["uri"], ao


def _process_repository(
    aspace_client: ASnakeClient,
    repo_id: int,
    dry_run: bool,
    print_output: bool,
    db_settings: dict,
    use_db: bool,
    limit: int | None,
    writer: ReportWriter,
) -> RunStats:
    """Remove Archivists' Toolkit (AT) External IDs from every archival object in
    a repository.

    Without use_db, pages through every archival object via the API and
    processes those with AT External IDs. With use_db, gets the candidate
    archival objects from the database; a dry run then needs no API calls at all.

    On Ctrl-C, in either path, stops and returns the totals so far. In a live
    run, the archival object being processed at that moment is logged, since its
    update may or may not have reached ArchivesSpace.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param int repo_id: Numeric ASpace repository ID.
    :param bool dry_run: If True, do not write changes to ArchivesSpace.
    :param bool print_output: If True, also print progress to the console.
    :param dict db_settings: DB connection details.
    :param bool use_db: If True, get candidates from the database.
    :param int | None limit: Stop after this many archival objects with AT IDs.
    :param ReportWriter writer: Destination for report rows.
    :return: RunStats with the run's totals.
    """
    stats = RunStats()
    processed = 0
    current_uri = None
    try:
        if dry_run and use_db:
            # Kept/other External ID counts would need a second query; not reported.
            stats.other_ids_kept = None
            for result in _iter_dry_run_results_from_db(
                db_settings, repo_id, print_output
            ):
                if limit is not None and processed >= limit:
                    break
                _record_result(result, dry_run, stats, writer)
                # Counted after recording, so an interrupted summary matches the CSV.
                stats.checked += 1
                processed += 1
                _log_progress_if_due(stats, repo_id, print_output)
        else:
            for uri, listed_ao in _iter_candidates(
                aspace_client, repo_id, db_settings, use_db, print_output
            ):
                if limit is not None and processed >= limit:
                    break
                stats.checked += 1

                # With the API listing, skip records without AT IDs before any call.
                if listed_ao is None or _get_AT_sources(listed_ao):
                    current_uri = uri
                    try:
                        result = _process_archival_object(
                            aspace_client, uri, dry_run, listed_ao
                        )
                    except (
                        Exception
                    ) as e:  # e.g. a dropped connection; retried next run
                        logger.exception(f"Unexpected error processing {uri}")
                        result = AOResult(
                            ao_uri=uri, error_message=f"{type(e).__name__}: {e}"
                        )
                    _record_result(result, dry_run, stats, writer)
                    # Cleared only once the result is fully recorded.
                    current_uri = None
                    processed += 1

                _log_progress_if_due(stats, repo_id, print_output)
    except KeyboardInterrupt:
        stats.interrupted = True
        message = "Interrupted: stopping"
        if current_uri and not dry_run:
            message += (
                f". {current_uri} was being processed: it may or may not have been "
                "updated, and may be missing from the change report"
            )
        _log_progress(message, True)

    return stats


# REPORTING


def _print_summary(
    repo_id: int,
    stats: RunStats,
    dry_run: bool,
    use_db: bool,
    print_output: bool,
) -> None:
    """Log a run summary and optionally print it to the console.

    :param int repo_id: The repository processed.
    :param RunStats stats: Totals for the run.
    :param bool dry_run: Whether this was a dry run (changes the wording).
    :param bool use_db: Whether candidates came from the database.
    :param bool print_output: If True, also print the summary to the console.
    """
    checked_label = (
        "Database candidates checked" if use_db else "Archival objects scanned"
    )
    verb = "would be removed" if dry_run else "removed"
    other_kept = (
        "not computed in a database dry run"
        if stats.other_ids_kept is None
        else stats.other_ids_kept
    )

    lines = [
        "DRY RUN: no changes written" if dry_run else "LIVE RUN",
        f"Candidates from: {'database' if use_db else 'API'}",
        f"Repository: {repo_id}",
        f"{checked_label}: {stats.checked}",
        f"Archival objects with AT External IDs: {stats.with_AT_ids}",
        f"Records with nothing to remove: {stats.nothing_to_remove}",
        f"Archival objects updated{' (would be)' if dry_run else ''}: {stats.updated}",
        f"AT External IDs {verb}: {stats.removed}",
        f"Other External IDs kept on those archival objects: {other_kept}",
        f"Errors: {stats.errors}",
        "Distinct AT sources matched (source: count):",
    ]
    lines += [
        f"  {source}: {count}" for source, count in stats.source_counts.most_common()
    ]
    if stats.interrupted:
        lines.append(
            "Run was INTERRUPTED. The report covers only records processed "
            "before the interruption."
            if dry_run
            else "Run was INTERRUPTED. With --use_db, run again to continue with "
            "the remaining records."
        )

    for line in lines:
        logger.info(line)
        if print_output:
            print(line)


# MAIN


def main() -> None:
    """Remove Archivists' Toolkit External IDs from a repository's archival objects."""
    args = _get_args()
    log_name = configure_logging(
        log_filename_stem=Path(__file__).stem, override_log_format=args.dry_run
    )
    print(f"Logging to {log_name}")
    # Name the CSVs after the timestamped log file, so each run (e.g. a resumed
    # one) keeps its own reports instead of overwriting the previous run's.
    report_base = Path(log_name).stem

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

    with ReportWriter(report_base) as writer:
        stats = _process_repository(
            aspace_client,
            args.repo_id,
            args.dry_run,
            args.print_output,
            db_settings,
            args.use_db,
            args.limit,
            writer,
        )
        for kind, path in writer.paths.items():
            logger.info(f"{kind.capitalize()} report written to {path}")

    _print_summary(args.repo_id, stats, args.dry_run, args.use_db, args.print_output)
    if stats.interrupted:
        # Shell convention: Ctrl-C sends SIGINT (signal 2). Bash returns exit code 128 + 2 = 130.
        # Python follows this convention for uncaught KeyboardInterrupt exceptions.
        # Use the same exit code (130) when the run is interrupted intentionally.
        sys.exit(130)


if __name__ == "__main__":
    main()
