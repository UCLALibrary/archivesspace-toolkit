import argparse
from pathlib import Path

from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient
import asnake.logging as logging

from add_alma_barcodes_to_archivesspace import print_unhandled_data
from migrate_alma_metadata_to_archivesspace import (
    SLFS_CODES,
    _print_summary,
    _resolve_slfs_location_refs,
    apply_alma_metadata,
    check_barcode,
    get_run_timestamps,
    match_alma_items_to_containers,
)
from utils import configure_logging, load_config, write_to_cache
from utils.alma_utils import get_alma_items
from utils.aspace_utils import get_aspace_containers, get_required_db_settings

# Logger available globally within this module.
# Configuration is done by configure_logging(), called in main().
logger = logging.get_logger(Path(__file__).stem)


def _get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Add Alma barcodes and migrate Alma metadata to ArchivesSpace "
            "top containers in one step."
        )
    )
    parser.add_argument("--bib_id", required=True, help="Alma bib MMS ID")
    parser.add_argument("--holdings_id", required=True, help="Alma holdings MMS ID")
    parser.add_argument(
        "--resource_id", required=True, help="ArchivesSpace resource ID"
    )
    parser.add_argument(
        "--repo_id",
        required=False,
        default=2,
        help="ArchivesSpace repository ID (default: 2)",
    )
    parser.add_argument("--profile", required=True, help="Matching profile module name")
    parser.add_argument("--config_file", required=True, help="Path to config YAML")
    parser.add_argument(
        "--use_db",
        action="store_true",
        help=(
            "Get ASpace containers from database instead of API (for large collections). "
            "Database settings are required either way."
        ),
    )
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
    parser.add_argument(
        "--use_cache",
        action="store_true",
        help="Read Alma and ASpace data from cached files if available",
    )
    return parser.parse_args()


def main() -> None:
    """Add barcodes and migrate metadata from Alma to matched ASpace top containers."""
    logging_filename_base = Path(__file__).stem
    print(f"Logging to {logging_filename_base}.log")

    args = _get_args()
    configure_logging(log_filename_stem=logging_filename_base, dry_run=args.dry_run)

    config = load_config(args.config_file)
    # Required (even without --use_db) to correctly identify false duplicates.
    db_settings = get_required_db_settings(config)
    alma_client = AlmaAPIClient(config["alma_config"]["alma_api_key"])
    aspace_client = ASnakeClient(**config)

    # Resolve SLF-S location refs once at startup; fail early if any are missing.
    slfs_location_refs = _resolve_slfs_location_refs(aspace_client, SLFS_CODES)

    alma_items = get_alma_items(
        alma_client, args.bib_id, args.holdings_id, args.use_cache
    )
    logger.info(f"Found {len(alma_items)} items in Alma")

    aspace_containers = get_aspace_containers(
        aspace_client, args.repo_id, args.resource_id, args.use_db, args.use_cache
    )
    logger.info(f"Found {len(aspace_containers)} top containers in ASpace")

    barcode_to_alma_item: dict[str, dict] = {
        item["barcode"]: item for item in alma_items if item.get("barcode")
    }

    matched_aspace_containers, original_barcodes, unhandled_data = (
        match_alma_items_to_containers(
            db_settings, alma_items, aspace_containers, args.profile
        )
    )

    timestamp, today = get_run_timestamps()

    skipped_location: list[str] = []
    skipped_profile: list[str] = []
    barcodes_added: list[str] = []
    barcodes_already_present: list[str] = []
    updated: list[str] = []
    failed: list[str] = []

    for tc in matched_aspace_containers:
        barcode = tc.get("barcode")
        alma_item = barcode_to_alma_item.get(barcode) if barcode else None
        if not alma_item:
            logger.warning(
                f"No Alma item found for barcode '{barcode}' on top container "
                f"{tc.get('uri')}; skipping"
            )
            continue

        uri = tc.get("uri", "")
        original_barcode = original_barcodes.get(uri)
        # Never overwrite an existing, different barcode.
        if barcode and not check_barcode(tc, original_barcode, barcode, unhandled_data):
            continue
        adding_barcode = not original_barcode

        location_skipped, profile_skipped = apply_alma_metadata(
            tc, alma_item, args.holdings_id, timestamp, today, slfs_location_refs
        )
        if location_skipped:
            skipped_location.append(uri)
        if profile_skipped:
            skipped_profile.append(uri)

        if args.dry_run:
            if adding_barcode:
                logger.info(f"Dry run: would add barcode to top container {uri}")
            updated.append(uri)
            (barcodes_added if adding_barcode else barcodes_already_present).append(uri)
            continue

        response = aspace_client.post(uri, json=tc)
        if response.status_code != 200:
            logger.error(
                f"Failed to update top container {uri}: "
                f"{response.status_code} {response.text}"
            )
            failed.append(uri)
            continue

        updated.append(uri)
        if adding_barcode:
            # Same message as add_alma_barcodes_to_archivesspace.py, so this log
            # works with that script's --undo_barcoding --use_log option.
            logger.info(f"Added barcode to top container {uri}")
            barcodes_added.append(uri)
        else:
            barcodes_already_present.append(uri)
        logger.info(f"Updated metadata for top container {uri}")

    _print_summary(
        alma_items,
        aspace_containers,
        matched_aspace_containers,
        skipped_location,
        skipped_profile,
        unhandled_data,
        args.print_output,
    )
    prefix = "Dry run: would have " if args.dry_run else ""
    extra_lines = [
        f"{prefix}Updated top containers: {len(updated)}",
        f"{prefix}Added barcodes: {len(barcodes_added)}",
        f"Top containers already barcoded (matching Alma): {len(barcodes_already_present)}",
        f"Failed updates: {len(failed)}",
    ]
    for line in extra_lines:
        logger.info(line)
        if args.print_output:
            print(line)

    if args.print_output:
        print()
        print_unhandled_data(unhandled_data)

    if unhandled_data:
        unhandled_filename = f"unhandled_{logging_filename_base}.json"
        write_to_cache(unhandled_data, unhandled_filename, indent=2)
        logger.info(f"Unhandled data written to {unhandled_filename}")


if __name__ == "__main__":
    main()
