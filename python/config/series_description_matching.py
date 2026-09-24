from typing import Any, Optional
import re

from config.base_match import build_match_data, normalize_alma_indicator


def parse_aspace_indicator(tc_indicator_with_series: str) -> tuple[str, str]:
    """Parses ASpace top container indicator with series into indicator and series.
    Returns a tuple with the indicator and series."""

    # check if the indicator starts with a digit - format should be 123XYZ
    if tc_indicator_with_series[0].isdigit():
        parsed_indicators = re.findall(r"(\d+)(\w+)", tc_indicator_with_series)
        # if we have no matches or more than one match, indicator is not in the expected format
        if len(parsed_indicators) != 1:
            return "", ""
        tc_indicator, tc_series = parsed_indicators[0]

    # otherwise, format should be XYZ-123
    else:
        parsed_indicators = re.findall(r"(\w+)-(\d+)", tc_indicator_with_series)
        if len(parsed_indicators) != 1:
            return "", ""
        tc_series, tc_indicator = parsed_indicators[0]
    return tc_indicator, tc_series


def _get_aspace_key(tc: dict, logger: Optional[Any] = None):
    """Returns the (indicator, type, series) matching key for an ASpace top container.

    If the indicator can't be parsed, logs an error and returns the container's URI
    instead. That key is unique, so the container won't match any Alma item and will
    be reported as unhandled data.
    """
    tc_type = tc.get("type")
    tc_indicator_with_series = tc.get("indicator", "")
    tc_indicator, tc_series = parse_aspace_indicator(tc_indicator_with_series)

    # normalize capitalization of series - all uppercase
    if tc_series:
        tc_series = tc_series.upper()

    if not tc_series or not tc_indicator:
        if logger:
            logger.error(
                f"Top container {tc.get('uri')} has an incorrect indicator format:"
                f" {tc_indicator_with_series}."
            )
        return tc.get("uri")

    return (tc_indicator, tc_type, tc_series)


def _get_alma_key(item: dict, logger: Optional[Any] = None) -> tuple:
    """Returns the (indicator, type, series) matching key for an Alma item, parsed
    from its space- and period-delimited description,
    e.g. "ser.P box.0011" -> ("11", "box", "P")."""
    description = item.get("description", "")
    alma_series = description.split(" ")[0].split(".")[1]
    alma_type = description.split(" ")[1].split(".")[0]
    alma_indicator = description.split(" ")[1].split(".")[1]

    # normalize capitalization of series - all uppercase
    alma_series = alma_series.upper()
    alma_indicator = normalize_alma_indicator(alma_indicator)

    return (alma_indicator, alma_type, alma_series)


def _format_duplicate_tc(tc: dict, key) -> tuple:
    """Reports a duplicate top container as (uri, indicator, type, series)."""
    return (tc.get("uri"), *key) if isinstance(key, tuple) else (tc.get("uri"), key)


def _format_duplicate_item(item: dict, key) -> tuple:
    """Reports a duplicate Alma item as (pid, indicator, type, series)."""
    return (item.get("pid"), *key) if isinstance(key, tuple) else (item.get("pid"), key)


def get_aspace_match_data(
    aspace_containers: list, logger: Optional[Any] = None
) -> tuple[dict, list[tuple]]:
    """Parses ASpace top container indicators into indicator and series and extracts
    the type. Returns a dictionary with the indicator, type, and series as keys, and a
    list of top containers with duplicate keys, as (uri, indicator, type, series)
    tuples. All top containers sharing a key are excluded from the match data.
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
) -> tuple[dict, list[tuple]]:
    """Parses Alma item descriptions into container type, indicator, and series
    and normalizes the indicator by removing leading zeroes and trailing " RESTRICTED".
    Returns a dictionary with the normalized indicator, type, and series as keys, and
    a list of items with duplicate keys, as (pid, indicator, type, series) tuples.
    All items sharing a key are excluded from the match data.
    """
    return build_match_data(
        alma_items,
        get_key=_get_alma_key,
        record_label="Alma item",
        id_field="pid",
        format_duplicate=_format_duplicate_item,
        logger=logger,
    )
