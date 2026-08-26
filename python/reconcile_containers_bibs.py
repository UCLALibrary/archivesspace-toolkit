import argparse
import structlog
from datetime import datetime

from alma_api_client import AlmaAPIClient
from asnake.client import ASnakeClient

from utils import configure_logging, load_config, write_dicts_to_csv
from utils.alma_utils import get_alma_items_from_alma
from utils.aspace_utils import (
    get_ao_titles_for_top_container_from_db,
    get_container_refs_from_db,
)

from config.base_match import match_containers
from config import (
    indicator_type_matching,
    indicator_only_matching,
    series_description_matching,
)

# Each profile module exposes get_aspace_match_data() and get_alma_match_data()
# with the same signature, so we can call them generically in the main program.
MATCH_PROFILES = {
    "indicator_type": indicator_type_matching,
    "indicator_only": indicator_only_matching,
    "series_description": series_description_matching,
}


def _get_args() -> argparse.Namespace:
    """Get command-line arguments for this program."""
    parser = argparse.ArgumentParser(
        description=(
            "Using ArchivesSpace and Alma IDs for the same LSC collection, "
            "produce two CSV reports: Alma items with no matching ArchivesSpace "
            "top container, and ArchivesSpace top containers with no matching "
            "Alma item."
        )
    )
    parser.add_argument(
        "-c",
        "--config_file",
        type=str,
        required=True,
        help=(
            "Path to YAML config file with ArchivesSpace credentials, "
            "including database connection settings and Alma API key."
        ),
    )
    parser.add_argument(
        "--repo_id",
        type=int,
        required=False,
        default=2,
        help="ArchivesSpace repository ID. Defaults to 2.",
    )
    parser.add_argument(
        "-r",
        "--resource_id",
        type=int,
        required=True,
        help="ArchivesSpace resource ID (i.e. collection) to process.",
    )
    parser.add_argument(
        "--bib_id",
        type=str,
        required=True,
        help="Alma bib MMS ID for the same collection.",
    )
    parser.add_argument(
        "--holdings_id",
        type=str,
        required=True,
        help="Alma holdings MMS ID for the same collection.",
    )
    parser.add_argument(
        "--match_profile",
        type=str,
        required=False,
        default="indicator_type",
        choices=sorted(MATCH_PROFILES.keys()),
        help=(
            "Which config profile to use for matching Alma items to ASpace "
            "top containers. 'indicator_type' (default) matches on "
            "container indicator + type. 'indicator_only' matches on "
            "indicator alone. 'series_description' additionally parses a "
            "series out of the indicator (e.g. 'ABC-123' or '123ABC') and "
            "requires Alma descriptions in the corresponding "
            "'ser.<series> <type>.<indicator>' format. See the config/ "
            "profile modules for exact matching and normalization logic."
        ),
    )
    return parser.parse_args()


def _get_all_top_containers_for_resource(
    aspace_client: ASnakeClient,
    db_config: dict,
    resource_id: int,
) -> list[dict]:
    """Fetch all top containers linked to the given resource, with their
    current location (if any) resolved inline.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param dict db_config: DB connection settings.
    :param int resource_id: The ID of the resource to process.
    :return: A list of top container dictionaries.
    """
    container_refs = get_container_refs_from_db(db_config, resource_id)

    all_tcs: list[dict] = []
    for ref in container_refs:
        try:
            # Resolve container_locations inline so we don't need a separate
            # API call per location to get a human-readable value.
            tc = aspace_client.get(
                ref, params={"resolve[]": "container_locations"}
            ).json()
        except Exception as err:
            print(f"Error fetching top container {ref}: {err}. Skipping.")
            continue
        all_tcs.append(tc)
    return all_tcs


def _get_current_location_title(top_container: dict) -> str:
    """Return the title of the top container's current location, if any.

    :param dict top_container: A top container dict.
    :return str: The current location's title, or "" if none is listed.
    """
    for location_ref in top_container.get("container_locations", []):
        if location_ref.get("status") != "current":
            continue
        resolved_location = location_ref.get("_resolved") or {}
        return resolved_location.get("title", "")
    return ""


def _get_top_container_id(top_container: dict) -> int | None:
    """Extract the numeric top container ID from its URI.

    :param dict top_container: A top container dict.
    :return int | None: The top container's numeric ID, or None if one
        can't be parsed from its URI.
    """
    uri = top_container.get("uri", "")
    try:
        return int(uri.rstrip("/").rsplit("/", 1)[-1])
    except (ValueError, IndexError):
        return None


def _get_linked_ao_titles(top_container: dict, db_config: dict) -> str:
    """Return a semicolon-separated list of archival object titles linked
    to the given top container, via a DB query.

    :param dict top_container: A top container dict.
    :param dict db_config: DB connection settings.
    :return str: Semicolon-separated archival object titles, or "" if the
        top container's ID couldn't be determined or it has no linked
        archival objects. Individual titles may themselves be "" if the
        underlying archival object has a blank title (see
        get_ao_titles_for_top_container_from_db).
    """
    tc_id = _get_top_container_id(top_container)
    if tc_id is None:
        print(
            f"Could not parse top container ID from URI "
            f"{top_container.get('uri')!r}; leaving linked AOs blank."
        )
        return ""
    ao_titles = get_ao_titles_for_top_container_from_db(db_config, tc_id)
    return "; ".join(ao_titles)


def _resolve_duplicate_container(
    duplicate: dict | tuple, containers_by_uri: dict[str, dict]
) -> dict:
    """Normalize a duplicate-container entry to a full container dict,
    regardless of which matching profile produced it.

    `indicator_type_matching` returns full dicts for duplicates. Other
    profiles (`indicator_only_matching`, `series_description_matching`)
    return identifying tuples whose first element is the container's URI.
    This looks the full record back up from the originally fetched
    containers so downstream report-building code only has to handle dicts.

    :param dict | tuple duplicate: A duplicate entry as returned by the
        active profile's get_aspace_match_data().
    :param dict[str, dict] containers_by_uri: Lookup of all fetched
        top containers by URI.
    :return dict: The full container dict, or a minimal stand-in dict
        with just the URI if it can't be found.
    """
    if isinstance(duplicate, dict):
        return duplicate
    uri = duplicate[0]
    return containers_by_uri.get(uri, {"uri": uri})


def _resolve_duplicate_item(duplicate: dict | tuple, items_by_pid: dict) -> dict:
    """Normalize a duplicate-item entry to a full Alma item dict,
    regardless of which matching profile produced it. See
    `_resolve_duplicate_container` for why this is needed.

    :param dict | tuple duplicate: A duplicate entry as returned by the
        active profile's get_alma_match_data().
    :param dict items_by_pid: Lookup of all fetched Alma items by pid.
    :return dict: The full item dict, or a minimal stand-in dict with
        just the pid if it can't be found.
    """
    if isinstance(duplicate, dict):
        return duplicate
    pid = duplicate[0]
    return items_by_pid.get(pid, {"pid": pid})


def _get_aspace_resource_info(
    aspace_client: ASnakeClient,
    resource_uri: str,
) -> tuple[str, str]:
    """Get the human-readable identifier and title for the given resource.

    :param ASnakeClient aspace_client: An authenticated ASnakeClient instance.
    :param str resource_uri: The URI of the resource to process.
    :return tuple[str, str]: A tuple of the human-readable identifier and the title of the resource.
    """
    resource = aspace_client.get(resource_uri).json()
    # ArchiveSpace resource object has 4 identifier fields, `id_0` to `id_3`.
    # Join all available parts to get human-readable identifier.
    human_readable_id = "-".join(
        [resource.get(f"id_{i}", "") for i in range(4) if resource.get(f"id_{i}", "")]
    )
    return human_readable_id, resource.get("title", "")


def _prepare_alma_missing_report_rows(
    unmatched_alma_items: list[dict],
    alma_bib_id: str,
    aspace_resource_uri: str,
    aspace_resource_id_human_readable: str,
    aspace_resource_title: str,
) -> list[dict]:
    """Prepare CSV row dicts for Alma items with no matching ASpace top container.

    :param list[dict] unmatched_alma_items: A list of Alma item dicts.
    :param str alma_bib_id: The Alma bib ID.
    :param str aspace_resource_uri: The URI of the ASpace resource.
    :param str aspace_resource_id_human_readable: The human readable identifier
    of the ASpace resource (e.g. "LSC--0293").
    :param str aspace_resource_title: The title of the ASpace resource.
    :return list[dict]: A list of CSV row dictionaries.
    """
    rows: list[dict] = []
    for alma_item in unmatched_alma_items:
        rows.append(
            {
                "Collection ID": aspace_resource_id_human_readable,
                "ASpace Resource URI": aspace_resource_uri,
                "ASpace Collection Title": aspace_resource_title,
                "Alma Bib ID": alma_bib_id,
                "Alma Item Barcode": alma_item.get("barcode", ""),
                "Alma Box Identifier": alma_item.get("description", ""),
            }
        )
    return rows


def _prepare_aspace_missing_report_rows(
    unmatched_aspace_containers: list[dict],
    aspace_resource_id_human_readable: str,
    aspace_resource_title: str,
    db_config: dict,
) -> list[dict]:
    """Prepare CSV row dicts for ASpace top containers with no matching Alma item.

    :param list[dict] unmatched_aspace_containers: A list of ASpace top container dicts.
    :param str aspace_resource_id_human_readable: The human readable identifier
    of the ASpace resource (e.g. "LSC--0293").
    :param str aspace_resource_title: The title of the ASpace resource.
    :param dict db_config: DB connection settings, used to look up each top
        container's linked archival objects.
    :return list[dict]: A list of CSV row dictionaries.
    """
    rows: list[dict] = []
    for tc in unmatched_aspace_containers:
        rows.append(
            {
                "Collection ID": aspace_resource_id_human_readable,
                "ASpace Collection Title": aspace_resource_title,
                "ASpace Top Container Indicator": tc.get("indicator", ""),
                "Container Type": tc.get("type", ""),
                "Location": _get_current_location_title(tc),
                "Linked Archival Objects": _get_linked_ao_titles(tc, db_config),
            }
        )
    return rows


def main() -> None:
    args = _get_args()
    config = load_config(args.config_file)

    # Standardize logging output the same way CSV/cache output is already
    # standardized (under the shared, host-mounted logs/ directory). This
    # must happen *before* ASnakeClient is instantiated: ASnakeClient
    # configures its own logging by default if structlog isn't already
    # configured, and its default log location isn't guaranteed to be
    # writable in every environment.
    configure_logging(log_filename_stem="reconcile_containers_bibs")
    logger = structlog.get_logger()

    aspace_client = ASnakeClient(**config)
    db_config = config.get("database")
    if not db_config:
        raise ValueError("DB connection settings are required.")

    print(
        f"Running container/item reconciliation for "
        f"ASpace resource ID: {args.resource_id}, "
        f"Alma Bib ID: {args.bib_id}, "
        f"Alma Holdings ID: {args.holdings_id}, "
        f"using match profile: {args.match_profile}"
    )

    # Get all top containers for the given collection from ASpace
    aspace_top_containers = _get_all_top_containers_for_resource(
        aspace_client, db_config, args.resource_id
    )
    print(
        f"Fetched {len(aspace_top_containers)} "
        f"top container{'s' if len(aspace_top_containers) > 1 else ''} from ASpace"
    )

    # For use in reports and output filenames
    aspace_resource_uri = f"/repositories/{args.repo_id}/resources/{args.resource_id}"
    aspace_resource_id_human_readable, aspace_resource_title = (
        _get_aspace_resource_info(aspace_client, aspace_resource_uri)
    )
    match_profile = MATCH_PROFILES[args.match_profile]
    aspace_match_data, raw_duplicate_aspace_containers = (
        match_profile.get_aspace_match_data(aspace_top_containers, logger=logger)
    )

    # Now get all items for the given collection from Alma
    alma_api_key = config["alma_config"]["alma_api_key"]
    alma_client = AlmaAPIClient(alma_api_key)
    alma_items = get_alma_items_from_alma(alma_client, args.bib_id, args.holdings_id)
    print(
        f"Fetched {len(alma_items)} "
        f"item{'s' if len(alma_items) > 1 else ''} from Alma"
    )
    alma_match_data, raw_duplicate_alma_items = match_profile.get_alma_match_data(
        alma_items, logger=logger
    )

    # Normalize duplicate entries to full dicts, since not every profile
    # returns them in that shape (see _resolve_duplicate_container/_item).
    containers_by_uri = {tc.get("uri"): tc for tc in aspace_top_containers}
    items_by_pid = {item.get("pid"): item for item in alma_items}
    duplicate_aspace_containers = [
        _resolve_duplicate_container(dup, containers_by_uri)
        for dup in raw_duplicate_aspace_containers
    ]
    duplicate_alma_items = [
        _resolve_duplicate_item(dup, items_by_pid) for dup in raw_duplicate_alma_items
    ]

    # Match in both directions at once
    _, unhandled_data = match_containers(
        alma_match_data, aspace_match_data, logger=logger
    )

    # Items/containers with duplicate keys were excluded from matching entirely
    # (see indicator_type_matching.py). Treat them as "missing" on their
    # respective side rather than silently dropping them from both reports.
    unmatched_alma_items = (
        unhandled_data.get("unmatched_alma_items", []) + duplicate_alma_items
    )
    unmatched_aspace_containers = (
        unhandled_data.get("unmatched_aspace_containers", [])
        + duplicate_aspace_containers
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Alma-missing report: Alma items with no matching ASpace top container
    if not unmatched_alma_items:
        print("No unmatched Alma items found.")
    else:
        print(
            f"Found {len(unmatched_alma_items)} Alma item(s) without a matching "
            f"ASpace top container ({len(duplicate_alma_items)} due to duplicate keys)."
        )
        alma_rows = _prepare_alma_missing_report_rows(
            unmatched_alma_items,
            args.bib_id,
            aspace_resource_uri,
            aspace_resource_id_human_readable,
            aspace_resource_title,
        )
        alma_output_path = write_dicts_to_csv(
            f"unmatched_alma_items_{aspace_resource_id_human_readable}_{timestamp}.csv",
            alma_rows,
        )
        print(f"Alma-missing-items CSV report written to {alma_output_path}")

    # ASpace-missing report: ASpace top containers with no matching Alma item
    if not unmatched_aspace_containers:
        print("No unmatched ASpace top containers found.")
    else:
        print(
            f"Found {len(unmatched_aspace_containers)} ASpace top container(s) without "
            f"a matching Alma item ({len(duplicate_aspace_containers)} due to duplicate keys)."
        )
        aspace_rows = _prepare_aspace_missing_report_rows(
            unmatched_aspace_containers,
            aspace_resource_id_human_readable,
            aspace_resource_title,
            db_config,
        )
        aspace_output_path = write_dicts_to_csv(
            f"unmatched_aspace_containers_{aspace_resource_id_human_readable}_{timestamp}.csv",
            aspace_rows,
        )
        print(f"ASpace-missing-containers CSV report written to {aspace_output_path}")


if __name__ == "__main__":
    main()
