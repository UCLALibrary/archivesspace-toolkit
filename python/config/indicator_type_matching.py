from typing import Any, Optional

from config.base_match import build_match_data, normalize_alma_indicator


def _get_aspace_key(tc: dict, logger: Optional[Any] = None) -> tuple:
    """Returns the (indicator, type) matching key for an ASpace top container."""
    return (tc.get("indicator"), tc.get("type"))


def _get_alma_key(item: dict, logger: Optional[Any] = None) -> tuple:
    """Returns the (indicator, container type) matching key for an Alma item,
    parsed from its description, e.g. "box.1"."""
    description = item.get("description", "")
    alma_container_type = description.split(".")[0]
    alma_indicator = normalize_alma_indicator(description.split(".")[1])
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
