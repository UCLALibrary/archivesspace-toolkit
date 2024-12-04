import unittest
import os
import json
from config.base_match import match_containers
from config.indicator_only_matching import get_alma_match_data, get_aspace_match_data


# Get the directory of the test file
current_dir = os.path.dirname(os.path.abspath(__file__))

# Construct absolute paths for the test data files
alma_data_path = os.path.join(current_dir, "alma_data_indicator_only.json")
aspace_data_path = os.path.join(current_dir, "aspace_data_indicator_only.json")

# Load the test data files
with open(alma_data_path, "r") as alma_data_file:
    alma_data = json.load(alma_data_file)

with open(aspace_data_path, "r") as aspace_data_file:
    aspace_data = json.load(aspace_data_file)


class TestBoxMapping(unittest.TestCase):
    # use description_matching versions of get_data functions

    def test_match_containers(self):
        # first item in alma_data should match first top container in aspace_data
        alma_items = [alma_data[0]]
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
        alma_items = [alma_data[1]]
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
        alma_items = [alma_data[2]]
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
        alma_items = [alma_data[3]]
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

    def test_match_containers_different_types(self):
        # fifth item in each set should match, even though alma description has different type
        # "Box 999" = "folder.999"
        alma_items = [alma_data[4]]
        aspace_containers = [aspace_data[4]]
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
