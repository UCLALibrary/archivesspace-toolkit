def match_containers(
    alma_items: list[dict], aspace_containers: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Match Alma items with ASpace top containers for the Tom Bradley collection.

    Args:
        alma_items: list of Alma items (JSON data as obtained from Alma API)
        aspace_containers: list of ASpace top containers (JSON data as obtained from ASpace API)

    Returns:
        tuple of lists containing 3 elements:
            matched_aspace_containers (list of JSON data elements with barcodes added),
            unmatched_alma_items (list of JSON data elements as obtained from Alma API),
            unmatched_aspace_containers (list of JSON data elements as obtained from ASpace API)
    """
    # use logger defined as a global in main script, if available
    # (will be None in tests)
    logger = globals().get("logger")

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
                    f"Matched item {alma_item.get('item_data').get('pid')}"
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


def _get_aspace_match_data(aspace_containers: list) -> dict[tuple]:
    match_data = {}
    for tc in aspace_containers:
        tc_indicator = tc.get("indicator")
        tc_type = tc.get("type")
        match_data[(tc_indicator, tc_type)] = tc
    return match_data


def _get_alma_match_data(alma_items: list) -> dict[tuple]:
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

        match_data[(alma_indicator, alma_container_type)] = item
    return match_data
