"""Generic utility functions for reuse across scripts."""

import argparse
import csv
import json
import sys
import yaml

from asnake import logging
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

# Shared output directories, relative to the current working directory
# (the script's launch directory — WORKDIR inside the toolkit's containers).
# These are expected to be mounted as volumes (e.g. via `docker compose`)
# so that logs, reports, and cache files land in predictable,
# host-accessible locations regardless of which script produced them.
LOGS_DIR = Path("logs")
OUTPUT_DIR = Path("output")
# Cache files are meant to be reused by later runs (via --use_cache),
# so they live in one fixed folder rather than in any single run's output folder.
CACHE_DIR = OUTPUT_DIR / "cache"

# Output folder for the current run, set by start_run().
# Scripts that don't call start_run() leave this as None,
# and their output goes directly in OUTPUT_DIR, as before.
_run_output_dir: Path | None = None


class RunPaths(NamedTuple):
    """Where a run started by start_run() writes its log and output files."""

    log_file: Path
    output_dir: Path


def resolve_output_path(filename: str | Path) -> Path:
    """Resolve a filename to a path in the current output folder, creating
    the folder if needed.

    The output folder is the current run's folder if start_run() has been called
    (e.g. output/<script>_resource_<id>_<timestamp>/), otherwise OUTPUT_DIR itself.
    Folders are only created when something is written to them, so runs that
    produce no output files leave no empty folders behind.

    Only the filename component of `filename` is used — any directory
    part supplied by a caller (e.g. a `reports/` prefix or a path from a
    CLI arg) is discarded, so that all script output consistently lands
    in the output folder regardless of how it's invoked.

    :param str | Path filename: A filename, or a path whose filename
        component should be resolved in the output folder.
    :return Path: The resolved path in the output folder.
    """
    output_dir = _run_output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / Path(filename).name


def _resolve_cache_path(filename: str | Path) -> Path:
    """Resolve a filename to a path under the shared CACHE_DIR, creating
    the directory if needed. As with resolve_output_path(),
    only the filename component of `filename` is used.

    :param str | Path filename: A filename, or a path whose filename
        component should be resolved under CACHE_DIR.
    :return Path: The resolved path under CACHE_DIR.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / Path(filename).name


def start_run(
    script_name: str,
    resource_id: str | int,
    args: argparse.Namespace,
    dry_run: bool = False,
) -> RunPaths:
    """Set up logging and output locations for one run of a script
    against a single ArchivesSpace resource (i.e. collection),
    and log the run's parameters.

    Both the log file and the run's output folder are named
    <script_name>_resource_<resource_id>_<timestamp>, e.g.:
        logs/merge_duplicate_containers_aspace_resource_1234_20261023_093000.log
        output/merge_duplicate_containers_aspace_resource_1234_20261023_093000/

    Like configure_logging(), this must be called before ASnakeClient is
    instantiated, so that ASnake does not fall back to its default log location.

    :param str script_name: Name of the script, usually Path(__file__).stem.
    :param str | int resource_id: The ASpace resource ID the script is run against.
    :param argparse.Namespace args: The script's parsed command-line arguments,
        all of which are logged.
    :param bool dry_run: Passed on to configure_logging(). Defaults to False.
    :return RunPaths: The paths of the log file and the run's output folder.
        The output folder is only created if the run writes a file to it.
    """
    global _run_output_dir

    run_name_stem = f"{script_name}_resource_{resource_id}"
    log_filename = configure_logging(log_filename_stem=run_name_stem, dry_run=dry_run)
    # Use the log file's name (which includes its timestamp) for the output folder,
    # so each run's log and output folder are guaranteed to match.
    _run_output_dir = OUTPUT_DIR / Path(log_filename).stem

    # Log both the command line as typed, and the full set of parameters
    # the script used, including defaults for any arguments not supplied.
    logging.get_logger(script_name).info(
        "Run parameters", command_line=sys.argv, parameters=vars(args)
    )

    run_paths = RunPaths(log_file=LOGS_DIR / log_filename, output_dir=_run_output_dir)
    print(f"Logging to {run_paths.log_file}")
    print(f"Output files, if any, will be written to {run_paths.output_dir}/")
    return run_paths


def configure_logging(
    log_filename_stem: str = "log",
    dry_run: bool = False,
) -> str:
    """Configure ASnake logging using the provided log filename stem.

    :param str log_filename_stem: The filename stem to use for the configured log file.
        Defaults to "log".
    :param bool dry_run: If True, write human-readable lines for review.
        If False, use ASnake's default JSON line format. Defaults to False.
    :return str: The name of the log file.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)  # create dir if it doesn't exist
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = LOGS_DIR / f"{log_filename_stem}_{timestamp}.log"

    logging.setup_logging(filename=log_filename, level="INFO")

    # structlog's TimeStamper defaults to UTC, but we want the system's local time.
    # The default config has a list of processors that includes a TimeStamper,
    # so we need to find that and replace it with our own,
    # with `utc=False` to use the system timezone.
    processors = logging.default_structlog_conf()["processors"]
    processors = [
        (
            logging.structlog.processors.TimeStamper(fmt="iso", utc=False)
            if isinstance(processor, logging.structlog.processors.TimeStamper)
            else processor
        )
        for processor in processors
    ]
    # For more human-readable output in dry run mode,
    # replace the `JSONRenderer` with a `ConsoleRenderer`
    # in structlog's processor list.
    # See docs @https://www.structlog.org/en/stable/console-output.html
    if dry_run:
        processors[-1] = logging.structlog.dev.ConsoleRenderer(colors=False)
    logging.structlog.configure(processors=processors)

    return log_filename.name


def load_config(config_file: str) -> dict:
    """Load the configuration file and return the config dictionary.

    :param str config_file: Path to YAML configuration file with connection details.
    :return dict: Config dict.
    """
    with open(config_file, "r") as f:
        return yaml.safe_load(f)


def write_dicts_to_csv(
    output_path: str | Path,
    rows: list[dict],
) -> Path:
    """Write a list of dictionaries to a CSV file in the current output folder
    (see resolve_output_path()), with each dict representing a row in the CSV.
    Fieldnames are derived from the first dict in the list.

    :param str | Path output_path: Filename (or path) for the CSV file. Only the
        filename component is used — the file is always written in the output folder.
    :param list[dict] rows: A list of CSV row dictionaries.
    :return Path: The resolved path the CSV was written to.
    """
    resolved_path = resolve_output_path(output_path)
    # Get the fieldnames from the first row
    fieldnames = list(rows[0].keys())
    with open(resolved_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return resolved_path


def read_from_cache(filename: str) -> list[dict] | None:
    """Reads data from the given file (under the shared CACHE_DIR) and returns it.
    Data is expected to be a list of dictionaries,
    but this method does not enforce that.

    :param str filename: Filename of cache file. Only the filename component
        is used — the file is always read from CACHE_DIR.
    :return: A list of dictionaries, or None if the cache file does not exist.
    """
    data_file = _resolve_cache_path(filename)
    if data_file.exists():
        with open(data_file, "r") as f:
            data = json.load(f)
    else:
        data = None
    return data


def write_to_cache(
    data: dict | list[dict],
    filename: str,
    indent: int | None = None,
) -> Path:
    """Stores data in the given file (under the shared CACHE_DIR) for possible later use.
    Data is expected to be a dict or list of dicts,
    but this method does not enforce that.

    For JSON reports that belong with a single run's output,
    use write_json_output() instead.

    :param dict | list[dict] data: Data to write to the cache file.
    :param str filename: Filename for cache file. Only the filename component
        is used — the file is always written under CACHE_DIR.
    :param int indent: Number of spaces to indent the JSON data.
        Defaults to None, which means no indentation.
    :return Path: The resolved path the cache file was written to.
    """
    resolved_path = _resolve_cache_path(filename)
    with open(resolved_path, "w") as f:
        json.dump(data, f, indent=indent)
    return resolved_path


def write_json_output(
    data: dict | list[dict],
    filename: str,
    indent: int | None = None,
) -> Path:
    """Writes data as JSON to the given file in the current output folder
    (see resolve_output_path()).

    :param dict | list[dict] data: Data to write.
    :param str filename: Filename for the JSON file. Only the filename component
        is used — the file is always written in the output folder.
    :param int indent: Number of spaces to indent the JSON data.
        Defaults to None, which means no indentation.
    :return Path: The resolved path the file was written to.
    """
    resolved_path = resolve_output_path(filename)
    with open(resolved_path, "w") as f:
        json.dump(data, f, indent=indent)
    return resolved_path
