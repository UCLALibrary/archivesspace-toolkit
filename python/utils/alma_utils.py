"""
Utility functions and helpers for working with Alma.

This module provides utilities for interacting with Alma
that can be reused across multiple scripts in the toolkit.
"""

from alma_api_client import AlmaAPIClient


def get_alma_items_from_alma(
    alma_client: AlmaAPIClient, bib_id: str, holdings_id: str
) -> list[dict]:
    """Returns item data from Alma for the given bib_id and holdings_id.
    The data is a list of dictionaries, each containing Alma data for one item.

    :param alma_client: AlmaAPIClient instance.
    :param str bib_id: Bib ID (AKA MMS ID) for the target collection.
    :param str holdings_id: Holdings ID for the target collection.
    :return: A list of dictionaries representing Alma items.
    """
    alma_items = []
    offset = 0
    # Get the total expected number of items
    try:
        response = alma_client.get_items(bib_id, holdings_id, {"limit": 1})
        response.raise_for_status()
        data = response.json()
        total_items = data.get("total_record_count", 0)
    except Exception as e:
        raise ValueError(f"Failed to get items from Alma: {e}")

    while len(alma_items) < total_items:
        try:
            response = alma_client.get_items(
                bib_id, holdings_id, {"limit": 100, "offset": offset}
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("item", [])
        except Exception as e:
            raise ValueError(f"Failed to get items from Alma: {e}")
        for item in items:
            alma_items.append(item.get("item_data"))
        offset += 100
    return alma_items
