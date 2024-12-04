from typing import Optional, Any


def get_aspace_match_data(
    aspace_containers: list, logger: Optional[Any] = None
) -> tuple[dict[tuple, list[tuple]]]:
    """Parses ASpace top container indicators into a dictionary."""
    match_data = {}
    tcs_with_duplicate_keys = []
    for tc in aspace_containers:
        tc_indicator = tc.get("indicator")
        # double check for duplicates
        if tc_indicator in match_data:
            if logger:
                logger.error(
                    f"Duplicate top container found: {tc_indicator} {tc.get('uri')}."
                    f" Existing top container: {match_data[tc_indicator].get('uri')}."
                    " Skipping both top containers."
                )
            tcs_with_duplicate_keys.append((tc.get("uri"), tc_indicator))
            tcs_with_duplicate_keys.append(
                (match_data[(tc_indicator)].get("uri"), tc_indicator)
            )
            # remove the duplicate
            del match_data[(tc_indicator)]
            # skip this top container
            continue
        match_data[tc_indicator] = tc
    return match_data, tcs_with_duplicate_keys


def get_alma_match_data(
    alma_items: list, logger: Optional[Any] = None
) -> tuple[dict[tuple], list[tuple]]:
    """Parses Alma item descriptions into indicators, and normalizes the indicator
    by removing leading zeroes and " RESTRICTED"."""
    match_data = {}
    items_with_duplicate_keys = []
    for item in alma_items:
        description = item.get("description")
        # split description into container type and indicator, e.g. "box.1"
        # keep only the indicator
        alma_indicator = description.split(".")[1]

        # if indicator starts with leading zeroes, remove them
        alma_indicator = alma_indicator.lstrip("0")

        # if indicator ends with " RESTRICTED", remove it
        if alma_indicator.endswith(" RESTRICTED"):
            alma_indicator = alma_indicator[:-11]

        # check if this will be a duplicate key
        if (alma_indicator) in match_data:
            current_item_pid = item.get("pid")
            previous_item_pid = match_data[alma_indicator].get("pid")
            if logger:
                logger.error(
                    f"Duplicate Alma indicator: {alma_indicator}"
                    f" for item {current_item_pid}."
                    f" Previous item with this indicator: {previous_item_pid}."
                    " Skipping both items."
                )
            items_with_duplicate_keys.append((current_item_pid, alma_indicator))
            items_with_duplicate_keys.append((previous_item_pid, alma_indicator))
            # remove the duplicate
            del match_data[alma_indicator]
            # skip this item
            continue
        match_data[alma_indicator] = item

    return match_data, items_with_duplicate_keys
