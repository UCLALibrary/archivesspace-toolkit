import unittest
import os
import json
from config.box_description_matching import match_containers

# Get the directory of the test file
current_dir = os.path.dirname(os.path.abspath(__file__))

# Construct absolute paths for the test data files
alma_data_path = os.path.join(current_dir, "alma_data_bradley.json")
aspace_data_path = os.path.join(current_dir, "aspace_data_bradley.json")

# Load the test data files
with open(alma_data_path, "r") as alma_data_file:
    alma_data = json.load(alma_data_file)

with open(aspace_data_path, "r") as aspace_data_file:
    aspace_data = json.load(aspace_data_file)


class TestBradleyMapping(unittest.TestCase):
    def test_match_containers(self):
        # first item in alma_data should match first top container in aspace_data
        alma_items = [alma_data[0]]
        aspace_containers = [aspace_data[0]]
        matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers = (
            match_containers(alma_items, aspace_containers)
        )
        self.assertEqual(len(matched_aspace_containers), 1)
        self.assertEqual(len(unmatched_alma_items), 0)
        self.assertEqual(len(unmatched_aspace_containers), 0)
        # test that the barcode was added to the matched top container
        self.assertEqual(
            matched_aspace_containers[0]["barcode"],
            alma_items[0]["item_data"]["barcode"],
        )

    def test_match_containers_no_match(self):
        # second item in each set should not match
        alma_items = [alma_data[1]]
        aspace_containers = [aspace_data[1]]
        matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers = (
            match_containers(alma_items, aspace_containers)
        )
        self.assertEqual(len(matched_aspace_containers), 0)
        self.assertEqual(len(unmatched_alma_items), 1)
        self.assertEqual(len(unmatched_aspace_containers), 1)

    def test_match_containers_leading_zeroes(self):
        # third item in each set should match, even though alma indicator has leading zeroes
        alma_items = [alma_data[2]]
        aspace_containers = [aspace_data[2]]
        matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers = (
            match_containers(alma_items, aspace_containers)
        )
        self.assertEqual(len(matched_aspace_containers), 1)
        self.assertEqual(len(unmatched_alma_items), 0)
        self.assertEqual(len(unmatched_aspace_containers), 0)
        # test that the barcode was added to the matched top container
        self.assertEqual(
            matched_aspace_containers[0]["barcode"],
            alma_items[0]["item_data"]["barcode"],
        )

    def test_match_containers_restricted(self):
        # fourth item in each set should match,
        # even though alma indicator has " RESTRICTED" at the end
        alma_items = [alma_data[3]]
        aspace_containers = [aspace_data[3]]
        matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers = (
            match_containers(alma_items, aspace_containers)
        )
        self.assertEqual(len(matched_aspace_containers), 1)
        self.assertEqual(len(unmatched_alma_items), 0)
        self.assertEqual(len(unmatched_aspace_containers), 0)
        # test that the barcode was added to the matched top container
        self.assertEqual(
            matched_aspace_containers[0]["barcode"],
            alma_items[0]["item_data"]["barcode"],
        )
