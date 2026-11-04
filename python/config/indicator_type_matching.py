from typing import Any, Optional

from config.base_match import (
    UnparseableAlmaKey,
    build_match_data,
    normalize_alma_indicator,
    skip_unparseable_alma_item,
)


def _get_aspace_key(tc: dict, logger: Optional[Any] = None) -> tuple:
    """Returns the (indicator, type) matching key for an ASpace top container."""
    return (tc.get("indicator"), tc.get("type"))


def _get_alma_key(
    item: dict[str, Any], logger: Optional[Any] = None
) -> tuple | UnparseableAlmaKey:
    """Returns the matching key for an Alma item: an (indicator, container type)
    tuple parsed from its description, to match an ASpace top container's
    `indicator` and `type`.

    Descriptions look like "<container type>.<indicator>". Both parts are used,
    and the indicator (box number) is normalized. For example:
        "box.23"            -> ("23", "box")
        "box.0024 RESTRICTED" -> ("24", "box")
        "folder.0025"         -> ("25", "folder")

    If there is no ".", or either part is empty (e.g. "sx142967-sx143045",
    "box.", or ".0023"), logs an error and returns a key that won't match
    anything, so the item is reported as unmatched.
    """
    description: str = item.get("description", "")
    parts = description.split(".")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return skip_unparseable_alma_item(item, "box.0023", logger)
    alma_container_type = parts[0]
    alma_indicator = normalize_alma_indicator(parts[1])
    return (alma_indicator, alma_container_type)


def get_aspace_match_data(
    aspace_containers: list[dict], logger: Optional[Any] = None
) -> tuple[dict[tuple, dict], list[dict]]:
    """Parses ASpace top container indicators and types into a dictionary.

    Top containers that share a normalized (indicator, type) key with another
    top container can't be reliably matched against Alma, so all of them are
    excluded from the returned match data and are instead returned in
    `duplicate_containers` — full container dicts, not just identifiers — so
    callers can still surface them (e.g. as "missing" in a reconciliation
    report) rather than silently dropping them.

    :param list[dict] aspace_containers: A list of ASpace top container dicts.
    :param logger: Optional logger for reporting duplicates.
    :return: A tuple of (match_data, duplicate_containers).
    """
    return build_match_data(
        aspace_containers,
        get_key=_get_aspace_key,
        record_label="top container",
        id_field="uri",
        logger=logger,
    )


def get_alma_match_data(
    alma_items: list[dict], logger: Optional[Any] = None
) -> tuple[dict[tuple, dict], list[dict]]:
    """Parses Alma item descriptions into container type and indicator,
    and normalizes the indicator by removing leading zeroes and " RESTRICTED".

    Items that share a normalized (indicator, container_type) key with another
    item can't be reliably matched against ASpace, so all of them are excluded
    from the returned match data and are instead returned in `duplicate_items` —
    full item dicts, not just identifiers — so callers can still surface them
    (e.g. as "missing" in a reconciliation report) rather than silently
    dropping them.

    :param list[dict] alma_items: A list of Alma item dicts.
    :param logger: Optional logger for reporting duplicates.
    :return: A tuple of (match_data, duplicate_items).
    """
    return build_match_data(
        alma_items,
        get_key=_get_alma_key,
        record_label="Alma item",
        id_field="pid",
        logger=logger,
    )
