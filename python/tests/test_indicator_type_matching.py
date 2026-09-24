import unittest
import copy
import os
import json
from config.base_match import match_containers
from config.indicator_type_matching import get_alma_match_data, get_aspace_match_data

# Get the directory of the test file
current_dir = os.path.dirname(os.path.abspath(__file__))

# Construct absolute paths for the test data files
alma_data_path = os.path.join(current_dir, "alma_data_indicator_type.json")
aspace_data_path = os.path.join(current_dir, "aspace_data_indicator_type.json")

# Load the test data files
with open(alma_data_path, "r") as alma_data_file:
    alma_data = json.load(alma_data_file)

with open(aspace_data_path, "r") as aspace_data_file:
    aspace_data = json.load(aspace_data_file)


class TestIndicatorTypeMapping(unittest.TestCase):
    # use indicator_type_description_matching versions of get_data functions

    def test_match_containers(self):
        # first item in alma_data should match first top container in aspace_data
        alma_items = [alma_data[0]["item_data"]]
        aspace_containers = [aspace_data[0]]
        alma_match_data, items_with_duplicate_keys = get_alma_match_data(alma_items)
        aspace_match_data, tcs_with_duplicate_keys = get_aspace_match_data(
            aspace_containers
        )
        matched_aspace_containers, unhandled_data = match_containers(
            alma_match_data,
            aspace_match_data,
        )
        self.assertEqual(len(matched_aspace_containers), 1)
        self.assertEqual(len(unhandled_data["unmatched_alma_items"]), 0)
        self.assertEqual(len(unhandled_data["unmatched_aspace_containers"]), 0)
        self.assertEqual(len(items_with_duplicate_keys), 0)
        self.assertEqual(len(tcs_with_duplicate_keys), 0)
        # test that the barcode was added to the matched top container
        self.assertEqual(
            matched_aspace_containers[0]["barcode"],
            alma_items[0]["barcode"],
        )

    def test_match_containers_no_match(self):
        # second item in each set should not match
        alma_items = [alma_data[1]["item_data"]]
        aspace_containers = [aspace_data[1]]
        alma_match_data, items_with_duplicate_keys = get_alma_match_data(alma_items)
        aspace_match_data, tcs_with_duplicate_keys = get_aspace_match_data(
            aspace_containers
        )

        matched_aspace_containers, unhandled_data = match_containers(
            alma_match_data, aspace_match_data
        )
        self.assertEqual(len(matched_aspace_containers), 0)
        self.assertEqual(len(unhandled_data["unmatched_alma_items"]), 1)
        self.assertEqual(len(unhandled_data["unmatched_aspace_containers"]), 1)
        self.assertEqual(len(items_with_duplicate_keys), 0)
        self.assertEqual(len(tcs_with_duplicate_keys), 0)

    def test_match_containers_leading_zeroes(self):
        # third item in each set should match, even though alma indicator has leading zeroes
        alma_items = [alma_data[2]["item_data"]]
        aspace_containers = [aspace_data[2]]
        alma_match_data, items_with_duplicate_keys = get_alma_match_data(alma_items)
        aspace_match_data, tcs_with_duplicate_keys = get_aspace_match_data(
            aspace_containers
        )
        matched_aspace_containers, unhandled_data = match_containers(
            alma_match_data, aspace_match_data
        )
        self.assertEqual(len(matched_aspace_containers), 1)
        self.assertEqual(len(unhandled_data["unmatched_alma_items"]), 0)
        self.assertEqual(len(unhandled_data["unmatched_aspace_containers"]), 0)
        self.assertEqual(len(items_with_duplicate_keys), 0)
        self.assertEqual(len(tcs_with_duplicate_keys), 0)

        # test that the barcode was added to the matched top container
        self.assertEqual(
            matched_aspace_containers[0]["barcode"],
            alma_items[0]["barcode"],
        )

    def test_match_containers_restricted(self):
        # fourth item in each set should match,
        # even though alma indicator has " RESTRICTED" at the end
        alma_items = [alma_data[3]["item_data"]]
        aspace_containers = [aspace_data[3]]
        alma_match_data, items_with_duplicate_keys = get_alma_match_data(alma_items)
        aspace_match_data, tcs_with_duplicate_keys = get_aspace_match_data(
            aspace_containers
        )
        matched_aspace_containers, unhandled_data = match_containers(
            alma_match_data, aspace_match_data
        )
        self.assertEqual(len(matched_aspace_containers), 1)
        self.assertEqual(len(unhandled_data["unmatched_alma_items"]), 0)
        self.assertEqual(len(unhandled_data["unmatched_aspace_containers"]), 0)
        self.assertEqual(len(items_with_duplicate_keys), 0)
        self.assertEqual(len(tcs_with_duplicate_keys), 0)
        # test that the barcode was added to the matched top container
        self.assertEqual(
            matched_aspace_containers[0]["barcode"],
            alma_items[0]["barcode"],
        )


class TestDuplicateKeys(unittest.TestCase):
    """Top containers or items sharing an (indicator, type) key are all excluded
    from matching, including the third and later ones
    (see config/base_match.build_match_data)."""

    def _copies(self, record: dict, id_field: str, count: int = 3) -> list[dict]:
        """Returns `count` copies of a record, with distinct identifiers."""
        copies = []
        for index in range(count):
            copy_of_record = copy.deepcopy(record)
            copy_of_record[id_field] = f"{record[id_field]}-{index}"
            copies.append(copy_of_record)
        return copies

    def test_three_aspace_containers_with_same_key(self):
        containers = self._copies(aspace_data[0], "uri")
        match_data, duplicate_containers = get_aspace_match_data(containers)
        self.assertEqual(match_data, {})
        # Full container dicts are returned, each exactly once.
        self.assertCountEqual(duplicate_containers, containers)

    def test_three_alma_items_with_same_key(self):
        items = self._copies(alma_data[0]["item_data"], "pid")
        match_data, duplicate_items = get_alma_match_data(items)
        self.assertEqual(match_data, {})
        self.assertCountEqual(duplicate_items, items)

    def test_third_duplicate_does_not_match(self):
        """The third container used to re-enter the match data and match an Alma
        item, giving one Alma item's data to an unrelated container."""
        containers = self._copies(aspace_data[0], "uri")
        alma_items = [alma_data[0]["item_data"]]
        alma_match_data, _ = get_alma_match_data(alma_items)
        aspace_match_data, _ = get_aspace_match_data(containers)
        matched_aspace_containers, unhandled_data = match_containers(
            alma_match_data, aspace_match_data
        )
        self.assertEqual(matched_aspace_containers, [])
        self.assertEqual(len(unhandled_data["unmatched_alma_items"]), 1)

    def test_containers_with_other_keys_still_match(self):
        containers = self._copies(aspace_data[0], "uri")
        containers.append(copy.deepcopy(aspace_data[2]))
        match_data, duplicate_containers = get_aspace_match_data(containers)
        self.assertEqual(list(match_data.values()), [aspace_data[2]])
        self.assertEqual(len(duplicate_containers), 3)
