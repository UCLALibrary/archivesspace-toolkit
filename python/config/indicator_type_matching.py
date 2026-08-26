from typing import Optional, Any


def get_aspace_match_data(
    aspace_containers: list[dict], logger: Optional[Any] = None
) -> tuple[dict[tuple, dict], list[dict]]:
    """Parses ASpace top container indicators and types into a dictionary.

    Top containers that share a normalized (indicator, type) key with another
    top container can't be reliably matched against Alma, so both are excluded
    from the returned match data and are instead returned in
    `duplicate_containers` — full container dicts, not just identifiers — so
    callers can still surface them (e.g. as "missing" in a reconciliation
    report) rather than silently dropping them.

    :param list[dict] aspace_containers: A list of ASpace top container dicts.
    :param logger: Optional logger for reporting duplicates.
    :return: A tuple of (match_data, duplicate_containers).
    """
    match_data: dict[tuple, dict] = {}
    duplicate_containers: list[dict] = []
    for tc in aspace_containers:
        tc_indicator = tc.get("indicator")
        tc_type = tc.get("type")
        key = (tc_indicator, tc_type)
        if key in match_data:
            if logger:
                logger.error(
                    f"Duplicate top container found: {tc_indicator} {tc_type} {tc.get('uri')}."
                    f" Existing top container: {match_data[key].get('uri')}."
                    " Excluding both from matching."
                )
            duplicate_containers.append(tc)
            duplicate_containers.append(match_data[key])
            # remove the duplicate
            del match_data[key]
            # skip this top container
            continue
        match_data[key] = tc
    return match_data, duplicate_containers


def get_alma_match_data(
    alma_items: list[dict], logger: Optional[Any] = None
) -> tuple[dict[tuple, dict], list[dict]]:
    """Parses Alma item descriptions into container type and indicator,
    and normalizes the indicator by removing leading zeroes and " RESTRICTED".

    Items that share a normalized (indicator, container_type) key with another
    item can't be reliably matched against ASpace, so both are excluded from
    the returned match data and are instead returned in `duplicate_items` —
    full item dicts, not just identifiers — so callers can still surface them
    (e.g. as "missing" in a reconciliation report) rather than silently
    dropping them.

    :param list[dict] alma_items: A list of Alma item dicts.
    :param logger: Optional logger for reporting duplicates.
    :return: A tuple of (match_data, duplicate_items).
    """
    match_data: dict[tuple, dict] = {}
    duplicate_items: list[dict] = []
    for item in alma_items:
        description = item.get("description", "")
        # split description into container type and indicator, e.g. "box.1"
        alma_container_type = description.split(".")[0]
        alma_indicator = description.split(".")[1]

        # if indicator starts with leading zeroes, remove them
        alma_indicator = alma_indicator.lstrip("0")

        # if indicator ends with " RESTRICTED", remove it
        if alma_indicator.endswith(" RESTRICTED"):
            alma_indicator = alma_indicator[:-11]

        key = (alma_indicator, alma_container_type)
        if key in match_data:
            if logger:
                logger.error(
                    f"Duplicate Alma description: {key}"
                    f" for item {item.get('pid')}."
                    f" Previous item with this description: {match_data[key].get('pid')}."
                    " Excluding both from matching."
                )
            duplicate_items.append(item)
            duplicate_items.append(match_data[key])
            # remove the duplicate
            del match_data[key]
            # skip this item
            continue
        match_data[key] = item

    return match_data, duplicate_items
