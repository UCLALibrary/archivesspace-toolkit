import argparse
import json
import asnake.logging as logging
from asnake.client import ASnakeClient
from pathlib import Path
from add_alma_barcodes_to_archivesspace import (
    _get_logger,
    _get_containers_from_container_refs,
)


def _get_args() -> argparse.Namespace:
    """Returns the command-line arguments for this program.

    :return argparse.Namespace: The parsed CLI arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resource_id",
        help="ArchivesSpace resource ID for target collection",
        required=True,
    )
    parser.add_argument(
        "--config_file",
        help="Path to config file with ASpace client credentials",
        required=True,
    )
    parser.add_argument(
        "--log_file",
        help="Path to log file with ASpace data",
        required=True,
    )
    parser.add_argument(
        "--dry_run",
        help="Dry run: do not update top containers in ArchivesSpace",
        action="store_true",
    )
    args = parser.parse_args()
    return args


def _get_container_refs_from_log_data(filename: str) -> set[str]:
    """Parses a log file and returns refs for containers that had barcodes added to them.

    :param filename: Filename of cache file with ASpace data.
    :return: A set of ASpace top container refs.
    """

    log_file = Path(filename)
    if not log_file.exists():
        logger.info("Input log file does not exist.")
        return None

    container_refs = []
    with open(log_file, "r") as f:
        for line in f:
            log_item = json.loads(line)
            event = log_item.get("event")
            if event and event.startswith("Added barcode to top container"):
                # parse top container ref from log event
                ref = event.split("Added barcode to top container").pop().strip()
                container_refs.append(ref)
    return set(container_refs)


def main() -> None:
    """Remove barcodes from collection that has already been barcoded."""

    logging_filename_base = Path(logging.handler.baseFilename).stem
    print(f"Logging to {logging_filename_base}.log")

    args = _get_args()

    aspace_client = ASnakeClient(config_file=args.config_file)

    # First, parse refs for containers that had barcodes added to them from log
    container_refs = _get_container_refs_from_log_data(args.log_file)
    # Then, use API to get containers from those refs.
    # This function is imported from `add_alma_barcodes_to_archivesspace.py`.
    aspace_containers = _get_containers_from_container_refs(
        aspace_client, container_refs
    )
    # Make sure returned containers have barcodes
    top_containers_with_barcodes = [tc for tc in aspace_containers if tc.get("barcode")]

    if args.dry_run:
        logger.info("Dry run: no changes made to ASpace top containers")
        logger.info(
            f"Would delete barcodes for {len(top_containers_with_barcodes)} top containers"
        )
    else:
        for tc in top_containers_with_barcodes:
            del tc["barcode"]
            aspace_client.post(tc["uri"], json=tc)
            logger.info(f"Deleted barcode for top container {tc['uri']}")
        logger.info(
            f"Deleted barcodes for {len(top_containers_with_barcodes)} top containers"
        )


if __name__ == "__main__":
    log_name = Path(__file__).stem
    logger = _get_logger(log_name)
    main()
