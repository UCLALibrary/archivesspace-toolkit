import argparse
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient
import asnake.logging as logging

from config.base_match import match_containers
from utils import configure_logging, load_config, write_to_cache
from utils.alma_utils import get_alma_items
from utils.aspace_utils import get_aspace_containers

# Logger available globally within this module.
# Configuration is done by configure_logging(), called in main().
logger = logging.get_logger(Path(__file__).stem)


# CONSTANTS

# Alma location codes for SRLF items that should have their location updated in ASpace.
SRLF_CODES = {"srar2", "srbi2", "sryr2", "sryr7"}

# Alma VolEquiv values (from Internal Note 1) mapped to ASpace container profile refs.
VOLEQUIV_TO_CONTAINER_PROFILE = {
    "1.92": "/container_profiles/127",  # half document box
    "3.85": "/container_profiles/128",  # document box
    "9.62": "/container_profiles/129",  # record carton
    "19.2": "/container_profiles/130",  # flat box
    "28.86": "/container_profiles/131",  # oversize flat box
}

# CLI


def _get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate Alma metadata fields to ArchivesSpace top containers."
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
        help="Get ASpace containers from database instead of API",
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


# DATA RETRIEVAL


def _resolve_srlf_location_refs(
    aspace_client: ASnakeClient, srlf_codes: set[str]
) -> dict[str, str]:
    """Build a mapping of SRLF location code to ASpace location ref at runtime.

    Pages through ASpace locations to find entries matching the known SRLF codes,
    returning early once all are resolved. Raises RuntimeError if any code cannot
    be found, so the script fails before touching any data rather than silently
    skipping location updates in the main loop.

    TODO: LSC to decide which ASpace field will be used to store the SRLF code.
    For now, this method checks the "barcode" field.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param set[str] srlf_codes: Alma location codes to resolve.
    :return: Dict mapping Alma location code to ASpace location ref string.
    """
    logger.info("Resolving ASpace location refs for SRLF codes.")
    resolved: dict[str, str] = {}

    for location in aspace_client.get_paged("/locations"):
        location_code = (
            location.get("barcode") or ""
        )  # TODO: replace with correct field
        if location_code in srlf_codes:
            resolved[location_code] = location["uri"]
            logger.info(f"Resolved SRLF code '{location_code}' to {location['uri']}")
        if len(resolved) == len(srlf_codes):
            break

    missing = srlf_codes - resolved.keys()
    if missing:
        raise RuntimeError(
            f"Could not resolve ASpace location refs for SRLF codes: {missing}. "
            "Verify these location codes exist in ASpace before running the script."
        )

    return resolved


# PARSING AND NORMALIZATION


def _parse_vol_equiv(raw: str) -> str:
    """Normalize a raw Alma Internal Note 1 value to a bare VolEquiv number.

    Handles both formats that may appear in the field:
      - "3.85" -> "3.85"
      - "VolEquiv=3.85" -> "3.85"

    :param str raw: Raw Internal Note 1 string from Alma.
    :return: Normalized VolEquiv string.
    """
    raw = raw.strip()
    if raw.upper().startswith("VOLEQUIV="):
        return raw.split("=", 1)[1].strip()
    return raw


def _apply_container_profile(tc: dict, alma_item: dict) -> bool:
    """Set the container_profile ref on a top container dict based on VolEquiv.

    Mutates tc in place. Returns True if the profile was applied, False if
    skipped due to a missing or unrecognized VolEquiv value.

    :param dict tc: ASpace top container dict to update.
    :param dict alma_item: Alma item dict for the matched item.
    :return: True if profile was applied, False otherwise.
    """
    raw = (alma_item.get("internal_note_1") or "").strip()
    if not raw:
        logger.warning(
            f"No VolEquiv (internal_note_1) found for item {alma_item.get('item_id')} "
            f"(barcode {alma_item.get('barcode')}); skipping container profile update"
        )
        return False

    vol_equiv = _parse_vol_equiv(raw)
    profile_ref = VOLEQUIV_TO_CONTAINER_PROFILE.get(vol_equiv)
    if not profile_ref:
        logger.warning(
            f"Unrecognized VolEquiv '{vol_equiv}' for item {alma_item.get('item_id')} "
            f"(barcode {alma_item.get('barcode')}); skipping container profile update"
        )
        return False
    logger.info(
        f"Setting container profile for top container {tc.get('uri')} "
        f"to {profile_ref} based on VolEquiv '{vol_equiv}'"
    )
    tc["container_profile"] = {"ref": profile_ref}
    return True


def _apply_ils_ids(tc: dict, alma_item: dict) -> None:
    """Set ILS Holding ID and ILS Item ID on a top container dict.

    Mutates tc in place.

    :param dict tc: ASpace top container dict to update.
    :param dict alma_item: Alma item dict for the matched item.
    """
    logger.info(
        f"Setting ILS Holding ID and ILS Item ID for top container {tc.get('uri')} "
        f"from Alma item {alma_item.get('item_id')} (barcode {alma_item.get('barcode')})"
    )
    tc["ils_holding_id"] = alma_item.get("holding_id", "")
    tc["ils_item_id"] = alma_item.get("item_id", "")


def _append_internal_note(tc: dict, timestamp: str) -> None:
    """Append a migration note to the top container's internal_note field,
    after a newline if there is existing content.

    :param dict tc: ASpace top container dict to update.
    :param str timestamp: ISO 8601 timestamp string for the migration note.
    """
    logger.info(
        f"Appending migration note to internal_note for top container {tc.get('uri')}"
    )
    migration_note = (
        f"Metadata updated in a scripted batch migration "
        f"from Alma item record on {timestamp}"
    )
    existing = (tc.get("internal_note") or "").strip()
    tc["internal_note"] = (
        f"{existing}\n{migration_note}" if existing else migration_note
    )


def _apply_location(
    tc: dict, location_code: str, location_refs: dict[str, str]
) -> bool:
    """Replace the container_locations array with a single current SRLF location.

    Mutates tc in place. Returns True if the location was applied, False if no
    ASpace ref is available for the given code.

    Replaces any pre-existing container_locations entries. For this migration
    context that is intentional: we are asserting the authoritative current
    location for SRLF items. The internal note records that this change was scripted.

    :param dict tc: ASpace top container dict to update.
    :param str location_code: Alma location code (already confirmed to be in SRLF_CODES).
    :param dict[str, str] location_refs: Resolved mapping of code to ASpace location ref.
    :return: True if location was applied, False otherwise.
    """
    location_ref = location_refs.get(location_code)
    if not location_ref:
        logger.warning(
            f"No ASpace location ref available for SRLF code '{location_code}'; "
            "skipping location update"
        )
        return False

    tc["container_locations"] = [
        {
            "jsonmodel_type": "container_location",
            "ref": location_ref,
            "status": "current",
            "start_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
    ]
    return True


# REPORTING


def _print_summary(
    alma_items: list[dict],
    aspace_containers: list[dict],
    matched: list[dict],
    skipped_location: list[str],
    skipped_profile: list[str],
    unhandled_data: dict,
    print_output: bool,
) -> None:
    """Log a run summary and optionally print it to the console.

    :param list[dict] alma_items: All Alma items fetched.
    :param list[dict] aspace_containers: All ASpace containers fetched.
    :param list[dict] matched: Matched ASpace containers.
    :param list[str] skipped_location: URIs of containers skipped for location update.
    :param list[str] skipped_profile: URIs of containers skipped for profile update.
    :param dict unhandled_data: Unmatched/duplicate items and containers.
    :param bool print_output: If True, also print to console.
    """
    summary_lines = [
        f"Total Alma items: {len(alma_items)}",
        f"Total ASpace top containers: {len(aspace_containers)}",
        f"Matched top containers: {len(matched)}",
        f"Location updates skipped (non-SRLF): {len(skipped_location)}",
        f"Container profile updates skipped (missing/unrecognized VolEquiv): \
            {len(skipped_profile)}",
        f"Unmatched Alma items: {len(unhandled_data.get('unmatched_alma_items', []))}",
        f"Unmatched ASpace top containers: \
            {len(unhandled_data.get('unmatched_aspace_containers', []))}",
        f"Alma items with duplicate keys: \
            {len(unhandled_data.get('items_with_duplicate_keys', []))}",
        f"ASpace top containers with duplicate keys: \
            {len(unhandled_data.get('tcs_with_duplicate_keys', []))}",
    ]
    for line in summary_lines:
        logger.info(line)
        if print_output:
            print(line)


# MAIN


def main() -> None:
    """Migrate Alma metadata fields to matched ArchivesSpace top containers."""
    logging_filename_base = Path(__file__).stem
    print(f"Logging to {logging_filename_base}.log")

    args = _get_args()
    configure_logging(log_filename_stem=logging_filename_base, dry_run=args.dry_run)

    config = load_config(args.config_file)
    alma_client = AlmaAPIClient(config["alma_config"]["alma_api_key"])
    aspace_client = ASnakeClient(**config)

    # Resolve SRLF location refs once at startup; fail early if any are missing.
    srlf_location_refs = _resolve_srlf_location_refs(aspace_client, SRLF_CODES)

    # Fetch source data.
    alma_items = get_alma_items(
        alma_client, args.bib_id, args.holdings_id, args.use_cache
    )
    logger.info(f"Found {len(alma_items)} items in Alma")

    aspace_containers = get_aspace_containers(
        aspace_client, args.repo_id, args.resource_id, args.use_db, args.use_cache
    )
    logger.info(f"Found {len(aspace_containers)} top containers in ASpace")

    # Build a barcode to Alma item lookup so we can retrieve Alma data for each
    # matched container. match_containers() writes the Alma barcode onto the ASpace
    # container dict; that barcode is then our join key back to the full Alma record.
    barcode_to_alma_item: dict[str, dict] = {
        item["barcode"]: item for item in alma_items if item.get("barcode")
    }

    # Match Alma items to ASpace top containers using the selected profile.
    profile_module = import_module(args.profile)
    get_alma_match_data = getattr(profile_module, "get_alma_match_data")
    get_aspace_match_data = getattr(profile_module, "get_aspace_match_data")

    aspace_match_data, tcs_with_duplicate_keys = get_aspace_match_data(
        aspace_containers, logger
    )
    alma_match_data, items_with_duplicate_keys = get_alma_match_data(alma_items, logger)
    matched_aspace_containers, unhandled_data = match_containers(
        alma_match_data, aspace_match_data, logger
    )
    unhandled_data["items_with_duplicate_keys"] = items_with_duplicate_keys
    unhandled_data["tcs_with_duplicate_keys"] = tcs_with_duplicate_keys

    # Single timestamp shared across all notes written in this run.
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    skipped_location: list[str] = []
    skipped_profile: list[str] = []

    for tc in matched_aspace_containers:
        barcode = tc.get("barcode")
        alma_item = barcode_to_alma_item.get(barcode) if barcode else None
        if not alma_item:
            # Shouldn't happen if match_containers() is working correctly,
            # but guard against it rather than raising an unhandled KeyError.
            logger.warning(
                f"No Alma item found for barcode '{barcode}' on top container "
                f"{tc.get('uri')}; skipping"
            )
            continue

        # Unconditional updates.
        _apply_ils_ids(tc, alma_item)
        _append_internal_note(tc, timestamp)
        if not _apply_container_profile(tc, alma_item):
            skipped_profile.append(tc["uri"])

        # Conditional update: location only for SRLF items.
        location_code = (alma_item.get("location").get("value") or "").strip().lower()
        if location_code in SRLF_CODES:
            if not _apply_location(tc, location_code, srlf_location_refs):
                skipped_location.append(tc["uri"])
        else:
            skipped_location.append(tc["uri"])
            logger.info(
                f"Skipped location update for {tc['uri']}: "
                f"location code '{location_code}' is not an SRLF code"
            )

        if not args.dry_run:
            aspace_client.post(tc["uri"], json=tc)
            logger.info(f"Updated metadata for top container {tc['uri']}")

    if args.dry_run:
        logger.info(
            f"Dry run: no changes written. Would have updated "
            f"{len(matched_aspace_containers)} top containers."
        )

    _print_summary(
        alma_items,
        aspace_containers,
        matched_aspace_containers,
        skipped_location,
        skipped_profile,
        unhandled_data,
        args.print_output,
    )

    if unhandled_data:
        unhandled_filename = f"unhandled_{logging_filename_base}.json"
        write_to_cache(unhandled_data, unhandled_filename, indent=2)
        logger.info(f"Unhandled data written to {unhandled_filename}")


if __name__ == "__main__":
    main()
