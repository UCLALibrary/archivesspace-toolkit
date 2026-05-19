"""Generic logging setup helpers for reuse across scripts."""

import asnake.logging as logging
import csv
import yaml
from datetime import datetime
from pathlib import Path


def configure_logging(log_filename_stem: str = "log") -> None:
    """Configure ASnake logging using the provided log filename stem.

    :param str log_filename_stem: The filename stem to use for the configured log file.
        Defaults to "log".
    """
    logs_dir = Path("logs")  # save logs to "./logs/"
    logs_dir.mkdir(parents=True, exist_ok=True)  # create dir if it doesn't exist
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = logs_dir / f"{log_filename_stem}_{timestamp}.log"
    logging.setup_logging(filename=log_filename, level="INFO")


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
