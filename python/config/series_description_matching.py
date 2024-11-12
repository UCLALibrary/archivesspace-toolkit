from typing import Optional, Any
import re


def parse_aspace_indicator(tc_indicator_with_series: str) -> tuple[str, str]:
    """Parses ASpace top container indicator with series into indicator and series.
    Returns a tuple with the indicator and series."""

    # check if the indicator is a digit - format should be 123XYZ
    if tc_indicator_with_series[0].isdigit():
        parsed_indicators = re.findall(r"(\d+)(\w+)", tc_indicator_with_series)
        # if we have no matches or more than one match, indicator is not in the expected format
        if len(parsed_indicators) != 1:
            return None, None
        (tc_indicator, tc_series) = parsed_indicators[0]

    # otherwise, format should be XYZ-123
    else:
        parsed_indicators = re.findall(r"(\w+)-(\d+)", tc_indicator_with_series)
        if len(parsed_indicators) != 1:
            return None, None
        (tc_series, tc_indicator) = parsed_indicators[0]
    return tc_indicator, tc_series


def get_aspace_match_data(
    aspace_containers: list, logger: Optional[Any] = None
) -> tuple[dict[tuple, list[tuple]]]:
    """Parses ASpace top container indicators into indicator and series and extracts the type.
    Returns a dictionary with the indicator, type, and series as keys, and a list of top
    containers with duplicate keys."""
    match_data = {}
    tcs_with_duplicate_keys = []
    for tc in aspace_containers:
        tc_type = tc.get("type")
        tc_indicator_with_series = tc.get("indicator")
        tc_indicator, tc_series = parse_aspace_indicator(tc_indicator_with_series)
        # if series or indicator is empty, there was a problem parsing the indicator
        # log an error, but don't skip the top container - it won't be matched and will be
        # included in the unhandled data.
        if not tc_series or not tc_indicator:
            if logger:
                logger.error(
                    f"Top container {tc.get('uri')} has an incorrect indicator format:"
                    f" {tc_indicator_with_series}."
                )

        # double check for duplicates only if we have a valid indicator and series
        elif (tc_indicator, tc_type, tc_series) in match_data:
            if logger:
                logger.error(
                    f"Duplicate top container found:"
                    f" {tc_indicator} {tc_type} {tc_series} {tc.get('uri')}."
                    f" Existing top container:"
                    f" {match_data[(tc_indicator, tc_type, tc_series)].get('uri')}."
                    " Skipping both top containers."
                )
            tcs_with_duplicate_keys.append(
                (tc.get("uri"), tc_indicator, tc_type, tc_series)
            )
            tcs_with_duplicate_keys.append(
                (
                    match_data[(tc_indicator, tc_type, tc_series)].get("uri"),
                    tc_indicator,
                    tc_type,
                    tc_series,
                )
            )
            # remove the duplicate
            del match_data[(tc_indicator, tc_type, tc_series)]
            # skip this top container
            continue
        match_data[(tc_indicator, tc_type, tc_series)] = tc
    return match_data, tcs_with_duplicate_keys


def get_alma_match_data(
    alma_items: list, logger: Optional[Any] = None
) -> tuple[dict[tuple], list[tuple]]:
    """Parses Alma item descriptions into container type, indicator, and series
    and normalizes the indicator by removing leading zeroes and trailing " RESTRICTED".
    Returns a dictionary with the normalized indicator, type, and series as keys, and
    a list of items with duplicate keys.
    """
    match_data = {}
    items_with_duplicate_keys = []
    for item in alma_items:
        description = item.get("description")
        # split description into series and container type/indicator (space and period delimited)
        # e.g. "ser.P box.0011" -> "P", "box", "0011"
        alma_series = description.split(" ")[0].split(".")[1]
        alma_type = description.split(" ")[1].split(".")[0]
        alma_indicator = description.split(" ")[1].split(".")[1]

        # if indicator starts with leading zeroes, remove them
        alma_indicator = alma_indicator.lstrip("0")

        # if indicator ends with " RESTRICTED", remove it
        if alma_indicator.endswith(" RESTRICTED"):
            alma_indicator = alma_indicator.replace(" RESTRICTED", "")

        # check if this will be a duplicate key
        if (alma_indicator, alma_type, alma_series) in match_data:
            current_item_pid = item.get("pid")
            previous_item_pid = match_data[
                (alma_indicator, alma_type, alma_series)
            ].get("pid")
            if logger:
                logger.error(
                    f"Duplicate Alma description: {(alma_indicator, alma_type, alma_series)} "
                    f" for item {current_item_pid}."
                    f" Previous item with this description: {previous_item_pid}."
                    " Skipping both items."
                )
            items_with_duplicate_keys.append(
                (current_item_pid, alma_indicator, alma_type, alma_series)
            )
            items_with_duplicate_keys.append(
                (previous_item_pid, alma_indicator, alma_type, alma_series)
            )
            # remove the duplicate
            del match_data[(alma_indicator, alma_type, alma_series)]
            # skip this item
            continue
        match_data[(alma_indicator, alma_type, alma_series)] = item

    return match_data, items_with_duplicate_keys
