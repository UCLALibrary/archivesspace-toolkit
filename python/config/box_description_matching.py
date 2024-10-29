from typing import Optional, Any


def match_containers(
    alma_items: list[dict], aspace_containers: list[dict], logger: Optional[Any] = None
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Matches Alma items with ASpace top containers and adds barcodes to the matched top containers.
    Also returns lists of unmatched Alma items and ASpace top containers.

    Args:
        alma_items: list of Alma items (JSON data as obtained from Alma API)
        aspace_containers: list of ASpace top containers (JSON data as obtained from ASpace API)

    Returns:
        tuple of lists containing 3 elements:
            matched_aspace_containers (list of JSON data elements with barcodes added),
            unmatched_alma_items (list of JSON data elements as obtained from Alma API),
            unmatched_aspace_containers (list of JSON data elements as obtained from ASpace API)
    """
    # get match data for Alma items and ASpace top containers
    alma_match_data = _get_alma_match_data(alma_items)
    aspace_match_data = _get_aspace_match_data(aspace_containers)

    # find matches by comparing keys in _match_data dictionaries
    matched_aspace_containers = []
    for alma_key, alma_item in alma_match_data.items():
        if alma_key in aspace_match_data:
            tc = aspace_match_data[alma_key]
            # get barcode from Alma item and add it to ASpace top container
            barcode = alma_item.get("item_data").get("barcode")
            tc["barcode"] = barcode
            matched_aspace_containers.append(tc)

            if logger:
                logger.info(
                    f"Matched item {alma_item.get('item_data').get('pid')} "
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

    return matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers


def _get_aspace_match_data(
    aspace_containers: list, logger: Optional[Any] = None
) -> dict[tuple]:
    """Parses ASpace top container indicators and types into a dictionary."""
    match_data = {}
    for tc in aspace_containers:
        tc_indicator = tc.get("indicator")
        tc_type = tc.get("type")
        # double check for duplicates
        if (tc_indicator, tc_type) in match_data:
            if logger:
                logger.error(
                    f"Duplicate top container found: {tc_indicator} {tc_type} {tc.get('uri')}.",
                    f" Existing top container: {match_data[(tc_indicator, tc_type)].get('uri')}.",
                    " Skipping both top containers.",
                )
            # remove the duplicate
            del match_data[(tc_indicator, tc_type)]
            # skip this top container
            continue
        match_data[(tc_indicator, tc_type)] = tc
    return match_data


def _get_alma_match_data(alma_items: list, logger: Optional[Any] = None) -> dict[tuple]:
    """Parses Alma item descriptions into container type and indicator,
    and normalizes the indicator by removing leading zeroes and " RESTRICTED"."""
    match_data = {}
    for item in alma_items:
        description = item.get("item_data").get("description")
        # split description into container type and indicator, e.g. "box.1"
        alma_container_type = description.split(".")[0]
        alma_indicator = description.split(".")[1]

        # if indicator starts with leading zeroes, remove them
        alma_indicator = alma_indicator.lstrip("0")

        # if indicator ends with " RESTRICTED", remove it
        if alma_indicator.endswith(" RESTRICTED"):
            alma_indicator = alma_indicator[:-11]

        # check if this will be a duplicate key
        if (alma_indicator, alma_container_type) in match_data:
            current_item_pid = item.get("item_data").get("pid")
            previous_item_pid = (
                match_data[(alma_indicator, alma_container_type)]
                .get("item_data")
                .get("pid")
            )
            if logger:
                logger.error(
                    f"Duplicate Alma description: {(alma_indicator, alma_container_type)}",
                    f" for item {current_item_pid}.",
                    f" Previous item with this description: {previous_item_pid}.",
                    " Skipping both items.",
                )
            # remove the duplicate
            del match_data[(alma_indicator, alma_container_type)]
            # skip this item
            continue
        match_data[(alma_indicator, alma_container_type)] = item
    return match_data
