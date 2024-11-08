from typing import Optional, Any


def match_containers(
    alma_items: list[dict], aspace_containers: list[dict], logger: Optional[Any] = None
) -> tuple[list[dict], dict[dict]]:
    """
    Matches Alma items with ASpace top containers and adds barcodes to the matched top containers.
    Also returns lists of unmatched Alma items and ASpace top containers.

    Args:
        alma_items: list of Alma items (JSON data as obtained from Alma API)
        aspace_containers: list of ASpace top containers (JSON data as obtained from ASpace API)

    Returns:
        tuple containing two elements:
            matched_aspace_containers - list of JSON data elements with barcodes added,
            unhandled_data - dict containing:
                unmatched_alma_items - list of unmatched items (JSON from Alma API),
                unmatched_aspace_containers - list of unmatched containers (JSON from ASpace API),
                items_with_duplicate_keys - list of Alma items with duplicate keys
                    (tuple of PID, indicator, type),
                tcs_with_duplicate_keys - list of JSON data elements with duplicate keys
                    (tuple of URI, indicator, type)
    """
    # get match data for Alma items and ASpace top containers
    alma_match_data, items_with_duplicate_keys = _get_alma_match_data(
        alma_items, logger
    )
    aspace_match_data, tcs_with_duplicate_keys = _get_aspace_match_data(
        aspace_containers, logger
    )

    # find matches by comparing keys in _match_data dictionaries
    matched_aspace_containers = []
    for alma_key, alma_item in alma_match_data.items():
        if alma_key in aspace_match_data:
            tc = aspace_match_data[alma_key]
            # get barcode from Alma item and add it to ASpace top container
            barcode = alma_item.get("barcode")
            tc["barcode"] = barcode
            matched_aspace_containers.append(tc)

            if logger:
                logger.info(
                    f"Matched item {alma_item.get('pid')} "
                    f"with top container {tc.get('uri')}"
                )

    # find unmatched Alma items and ASpace top containers
    alma_keys = set(alma_match_data.keys())
    aspace_keys = set(aspace_match_data.keys())

    unmatched_alma_keys = alma_keys - aspace_keys
    unmatched_aspace_keys = aspace_keys - alma_keys

    unmatched_alma_items = [alma_match_data[key] for key in unmatched_alma_keys]
    unmatched_aspace_containers = [
        aspace_match_data[key] for key in unmatched_aspace_keys
    ]

    # assemble unhandled data dict
    unhandled_data = {
        "unmatched_alma_items": unmatched_alma_items,
        "unmatched_aspace_containers": unmatched_aspace_containers,
        "items_with_duplicate_keys": items_with_duplicate_keys,
        "tcs_with_duplicate_keys": tcs_with_duplicate_keys,
    }

    return matched_aspace_containers, unhandled_data


def _get_aspace_match_data(
    aspace_containers: list, logger: Optional[Any] = None
) -> tuple[dict[tuple, list[tuple]]]:
    """Parses ASpace top container indicators into indicator and series and extracts the type.
    Returns a dictionary with the indicator, type, and series as keys, and a list of top
    containers with duplicate keys."""
    match_data = {}
    tcs_with_duplicate_keys = []
    for tc in aspace_containers:
        tc_indicator_with_series = tc.get("indicator")
        # if indicator begins with a number, split it into indicator and series
        # numerical part is the indicator, the rest is the series
        # e.g. "330M" -> indicator "330", series "M"
        if tc_indicator_with_series[0].isdigit():
            tc_indicator = ""
            for char in tc_indicator_with_series:
                if char.isdigit():
                    tc_indicator += char
                else:
                    break
            tc_series = tc_indicator_with_series[len(tc_indicator) :]
        # if indicator does not begin with a number, also split it into indicator and series
        # hyphen delimited
        # e.g. "SR-130" -> indicator "130", series "SR"
        else:
            tc_indicator = tc_indicator_with_series.split("-")[1]
            tc_series = tc_indicator_with_series.split("-")[0]
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


def _get_alma_match_data(
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
