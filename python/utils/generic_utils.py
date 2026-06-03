"""Generic utility functions for reuse across scripts."""

import csv
import json
import yaml

from asnake import logging
from datetime import datetime
from pathlib import Path


def configure_logging(
    log_filename_stem: str = "log",
    dry_run: bool = False,
) -> None:
    """Configure ASnake logging using the provided log filename stem.

    :param str log_filename_stem: The filename stem to use for the configured log file.
        Defaults to "log".
    :param bool dry_run: If True, write human-readable lines for review.
        If False, use ASnake's default JSON line format. Defaults to False.
    """
    logs_dir = Path("logs")  # save logs to "./logs/"
    logs_dir.mkdir(parents=True, exist_ok=True)  # create dir if it doesn't exist
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = logs_dir / f"{log_filename_stem}_{timestamp}.log"

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


def load_config(config_file: str) -> dict:
    """Load the configuration file and return the config dictionary.

    :param str config_file: Path to YAML configuration file with connection details.
    :return dict: Config dict.
    """
    with open(config_file, "r") as f:
        return yaml.safe_load(f)


def write_dicts_to_csv(
    output_path: Path,
    rows: list[dict],
) -> None:
    """Write a list of dictionaries to a CSV file,
    with each dict representing a row in the CSV.
    Fieldnames are derived from the first dict in the list.

    :param Path output_path: Path to write the CSV file.
    :param list[dict] rows: A list of CSV row dictionaries.
    """
    # Get the fieldnames from the first row
    fieldnames = list(rows[0].keys())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_from_cache(filename: str) -> list[dict] | None:
    """Reads data from the given file and returns it.
    Data is expected to be a list of dictionaries,
    but this method does not enforce that.

    :param str filename: Filename of cache file.
    :return: A list of dictionaries, or None if the cache file does not exist.
    """
    data_file = Path(filename)
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
) -> None:
    """Stores data in the given file for possible later use.
    Data is expected to be a dict or list of dicts,
    but this method does not enforce that.

    :param dict | list[dict] data: Data to write to the cache file.
    :param str filename: Filename for cache file.
    :param int indent: Number of spaces to indent the JSON data.
        Defaults to None, which means no indentation.
    """
    with open(filename, "w") as f:
        json.dump(data, f, indent=indent)
