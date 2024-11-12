from typing import Optional, Any
import re


def get_aspace_match_data(
    aspace_containers: list, logger: Optional[Any] = None
) -> tuple[dict[tuple, list[tuple]]]:
    """Parses ASpace top container indicators into indicator and series and extracts the type.
    Returns a dictionary with the indicator, type, and series as keys, and a list of top
    containers with duplicate keys."""
    match_data = {}
    tcs_with_duplicate_keys = []
    for tc in aspace_containers:
        tc_indicator_with_series = tc.get("indicator")
        # indicator_with_series may be formatted as "123XYZ" or "XYZ-123"
        if tc_indicator_with_series[0].isdigit():
            # DigitsLetter
            (tc_indicator, tc_series) = re.findall(
                r"(\d+)(\w+)", tc_indicator_with_series
            )[0]
        else:
            # Letters-Digits
            (tc_series, tc_indicator) = re.findall(
                r"(\w+)-(\d+)", tc_indicator_with_series
            )[0]

        tc_type = tc.get("type")
        # double check for duplicates
        if (tc_indicator, tc_type, tc_series) in match_data:
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
