from typing import Any, Callable, Optional


def match_containers(
    alma_match_data: dict,
    aspace_match_data: dict,
    logger: Optional[Any] = None,
) -> tuple[list[dict], dict[str, list[dict]]]:
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
    matched_aspace_containers: list[dict] = []
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

    unmatched_alma_items: list[dict] = [
        alma_match_data[key] for key in unmatched_alma_keys
    ]
    unmatched_aspace_containers: list[dict] = [
        aspace_match_data[key] for key in unmatched_aspace_keys
    ]

    # assemble unhandled data dict
    unhandled_data: dict[str, list[dict]] = {
        "unmatched_alma_items": unmatched_alma_items,
        "unmatched_aspace_containers": unmatched_aspace_containers,
    }

    return matched_aspace_containers, unhandled_data


def normalize_alma_indicator(alma_indicator: str) -> str:
    """Normalizes an indicator parsed from an Alma item description, by removing
    leading zeroes and a trailing " RESTRICTED".

    :param str alma_indicator: Indicator parsed from an Alma item description.
    :return: The normalized indicator.
    """
    alma_indicator = alma_indicator.lstrip("0")
    if alma_indicator.endswith(" RESTRICTED"):
        alma_indicator = alma_indicator.replace(" RESTRICTED", "")
    return alma_indicator


def _keep_record(record: dict, key: Any) -> dict:
    """Default duplicate formatter: report the full record."""
    return record


def build_match_data(
    records: list[dict],
    get_key: Callable[[dict, Optional[Any]], Any],
    record_label: str,
    id_field: str,
    format_duplicate: Callable[[dict, Any], Any] = _keep_record,
    logger: Optional[Any] = None,
) -> tuple[dict, list]:
    """Builds a dict of matching key -> record, excluding records whose key is shared
    with any other record.

    Each matching profile decides what the key is; the duplicate handling is the same
    for all of them. A key used by more than one record can't be matched reliably, so
    EVERY record with that key is excluded, including the third and later ones: the
    key is remembered after the first collision, not just removed from the match data.

    :param list[dict] records: ASpace top containers or Alma items.
    :param get_key: Callable taking (record, logger) and returning the record's
        matching key. Profiles log their own parsing problems and may return a
        deliberately unique key (e.g. a URI) for records they can't parse.
    :param str record_label: What the records are, for log messages,
        e.g. "top container" or "Alma item".
    :param str id_field: Field identifying a record in log messages,
        e.g. "uri" or "pid".
    :param format_duplicate: Callable taking (record, key) and returning the entry to
        add to the returned list of duplicates. Defaults to the whole record.
    :param logger: Optional logger for reporting duplicates.
    :return: A tuple of (match_data, duplicates).
    """
    match_data: dict = {}
    duplicates: list = []
    # Keys already known to be duplicated, so a 3rd or later record with the same key
    # is excluded too, rather than being added back to match_data.
    duplicate_keys: set = set()

    for record in records:
        key = get_key(record, logger)
        if key in duplicate_keys:
            if logger:
                logger.error(
                    f"Duplicate {record_label} found: {key}"
                    f" ({record.get(id_field)}). Excluding from matching."
                )
            duplicates.append(format_duplicate(record, key))
            continue

        if key in match_data:
            previous_record = match_data[key]
            if logger:
                logger.error(
                    f"Duplicate {record_label} found: {key}"
                    f" ({record.get(id_field)})."
                    f" Existing {record_label}: {previous_record.get(id_field)}."
                    " Excluding both from matching."
                )
            duplicates.append(format_duplicate(record, key))
            duplicates.append(format_duplicate(previous_record, key))
            del match_data[key]
            duplicate_keys.add(key)
            continue

        match_data[key] = record

    return match_data, duplicates
