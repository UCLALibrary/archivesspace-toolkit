def match_containers(alma_items: list, aspace_containers: list, logger) -> tuple:
    unmatched_alma_items = []
    matched_aspace_containers = []
    for item in alma_items:
        item_id = item.get("item_data").get("pid")
        barcode = item.get("item_data").get("barcode")
        description = item.get("item_data").get("description")
        alma_container_type = description.split(".")[0]
        alma_indicator = description.split(".")[1]
        # if indicator starts with 0, remove it
        while alma_indicator[0] == "0":
            alma_indicator = alma_indicator[1:]
        # if indicator ends with " RESTRICTED", remove it
        if alma_indicator.endswith(" RESTRICTED"):
            alma_indicator = alma_indicator[:-11]
        # match with ASpace top container based on container type and indicator
        for tc in aspace_containers:
            tc_type = tc.get("type")
            tc_indicator = tc.get("indicator")
            tc_uri = tc.get("uri")
            if tc_type == alma_container_type and tc_indicator == alma_indicator:
                if logger:
                    logger.info(f"Matched item {item_id} with top container {tc_uri}")
                # add barcode to top container
                tc["barcode"] = barcode
                matched_aspace_containers.append(tc)
                break
        else:
            if logger:
                logger.info(f"No match found for item {item_id}")
            unmatched_alma_items.append(item)
    # find unmatched ASpace top containers
    unmatched_aspace_containers = []
    for tc in aspace_containers:
        # items in matched_aspace_containers have the barcode added, so they won't match exactly
        # instead, check that the type and indicator match
        tc_type = tc.get("type")
        tc_indicator = tc.get("indicator")
        if not any(
            tc_type == matched_tc.get("type")
            and tc_indicator == matched_tc.get("indicator")
            for matched_tc in matched_aspace_containers
        ):
            unmatched_aspace_containers.append(tc)
    return matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers
