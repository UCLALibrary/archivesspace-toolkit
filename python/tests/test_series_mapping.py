import unittest
import os
import json
from config.base_match import match_containers
from config.series_description_matching import (
    get_alma_match_data as series_get_alma_match_data,
    get_aspace_match_data as series_get_aspace_match_data,
)

# Get the directory of the test file
current_dir = os.path.dirname(os.path.abspath(__file__))

# Construct absolute paths for the test data files
alma_data_path = os.path.join(current_dir, "alma_data_series.json")
aspace_data_path = os.path.join(current_dir, "aspace_data_series.json")

# Load the test data files
with open(alma_data_path, "r") as alma_data_file:
    alma_data = json.load(alma_data_file)

with open(aspace_data_path, "r") as aspace_data_file:
    aspace_data = json.load(aspace_data_file)


class TestSeriesMapping(unittest.TestCase):

    def test_match_containers_digits_first(self):
        # first item in alma_data should match first top container in aspace_data
        # "ser.C box.0025" = 25C
        alma_items = [alma_data[0]]
        aspace_containers = [aspace_data[0]]
        alma_match_data, items_with_duplicate_keys = series_get_alma_match_data(
            alma_items
        )
        aspace_match_data, tcs_with_duplicate_keys = series_get_aspace_match_data(
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

    def test_match_containers_digits_last(self):
        # second item in alma_data should match second top container in aspace_data
        # "ser.C box.0026" = 26-C
        alma_items = [alma_data[1]]
        aspace_containers = [aspace_data[1]]
        alma_match_data, items_with_duplicate_keys = series_get_alma_match_data(
            alma_items
        )
        aspace_match_data, tcs_with_duplicate_keys = series_get_aspace_match_data(
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

    def test_multiple_invalid_aspace_indicators(self):
        # third and fourth aspace_data items have an invalid top container indicator
        # "C-26, C-27, and C-28" and "INVALID" - should not match any alma_data items
        alma_items = alma_data[0:2]
        aspace_containers = aspace_data[2:4]
        alma_match_data, items_with_duplicate_keys = series_get_alma_match_data(
            alma_items
        )
        aspace_match_data, tcs_with_duplicate_keys = series_get_aspace_match_data(
            aspace_containers
        )
        matched_aspace_containers, unhandled_data = match_containers(
            alma_match_data,
            aspace_match_data,
        )
        self.assertEqual(len(matched_aspace_containers), 0)
        self.assertEqual(len(unhandled_data["unmatched_alma_items"]), 2)
        self.assertEqual(len(unhandled_data["unmatched_aspace_containers"]), 2)
        self.assertEqual(len(items_with_duplicate_keys), 0)
        self.assertEqual(len(tcs_with_duplicate_keys), 0)
