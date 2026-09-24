from typing import Any, Optional

from config.base_match import build_match_data, normalize_alma_indicator


def _get_aspace_key(tc: dict, logger: Optional[Any] = None) -> str:
    """Returns the indicator matching key for an ASpace top container."""
    return tc.get("indicator", "")


def _get_alma_key(item: dict, logger: Optional[Any] = None) -> str:
    """Returns the indicator matching key for an Alma item, parsed from its
    description, e.g. "box.1"."""
    description = item.get("description", "")
    return normalize_alma_indicator(description.split(".")[1])


def _format_duplicate_tc(tc: dict, key: str) -> tuple:
    """Reports a duplicate top container as (uri, indicator)."""
    return (tc.get("uri"), key)


def _format_duplicate_item(item: dict, key: str) -> tuple:
    """Reports a duplicate Alma item as (pid, indicator)."""
    return (item.get("pid"), key)


def get_aspace_match_data(
    aspace_containers: list, logger: Optional[Any] = None
) -> tuple[dict[str, dict], list[tuple]]:
    """Parses ASpace top container indicators into a dictionary.

    Top containers sharing an indicator with another top container are all
    excluded from the match data, and returned as (uri, indicator) tuples.

    :param list aspace_containers: A list of ASpace top container dicts.
    :param logger: Optional logger for reporting duplicates.
    :return: A tuple of (match_data, tcs_with_duplicate_keys).
    """
    return build_match_data(
        aspace_containers,
        get_key=_get_aspace_key,
        record_label="top container",
        id_field="uri",
        format_duplicate=_format_duplicate_tc,
        logger=logger,
    )


def get_alma_match_data(
    alma_items: list, logger: Optional[Any] = None
) -> tuple[dict[str, dict], list[tuple]]:
    """Parses Alma item descriptions into indicators, and normalizes the indicator
    by removing leading zeroes and " RESTRICTED".

    Items sharing an indicator with another item are all excluded from the match
    data, and returned as (pid, indicator) tuples.

    :param list alma_items: A list of Alma item dicts.
    :param logger: Optional logger for reporting duplicates.
    :return: A tuple of (match_data, items_with_duplicate_keys).
    """
    return build_match_data(
        alma_items,
        get_key=_get_alma_key,
        record_label="Alma item",
        id_field="pid",
        format_duplicate=_format_duplicate_item,
        logger=logger,
    )
