"""Generic logging setup helpers for reuse across scripts."""

import asnake.logging as logging
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
