from typing import Optional, Any


def match_containers(
    alma_match_data: dict,
    aspace_match_data: dict,
    logger: Optional[Any] = None,
) -> tuple[list[dict], dict[dict]]:
    """
    Matches Alma items with ASpace top containers and adds barcodes to the matched top containers.
    Also returns lists of unmatched Alma items and ASpace top containers.

    Args:
        alma_match_data: dictionary with keys to match against ASpace data
            and Alma JSON data as values
        aspace_match_data: dictionary with keys to match against Alma data
            and ASpace JSON data as values
    Returns:
        tuple containing two elements:
            matched_aspace_containers - list of JSON data elements with barcodes added,
            unhandled_data - dict containing:
                unmatched_alma_items - list of unmatched items (JSON from Alma API),
                unmatched_aspace_containers - list of unmatched containers (JSON from ASpace API)
    """

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
    }

    return matched_aspace_containers, unhandled_data
