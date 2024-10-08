import unittest
import sys
import os
import json


# Add the config directory to the sys.path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config"))
)
from bradley import match_containers


alma_data_path = "alma_data_bradley.json"
aspace_data_path = "aspace_data_bradley.json"

with open(alma_data_path, "r") as alma_data_file:
    alma_data = json.load(alma_data_file)

with open(aspace_data_path, "r") as aspace_data_file:
    aspace_data = json.load(aspace_data_file)


class TestBradleyMapping(unittest.TestCase):
    def test_match_containers(self):
        # first item in alma_data should match first top container in aspace_data
        alma_items = [alma_data[0]]
        aspace_containers = [aspace_data[0]]
        logger = None
        matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers = (
            match_containers(alma_items, aspace_containers, logger)
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
        logger = None
        matched_aspace_containers, unmatched_alma_items, unmatched_aspace_containers = (
            match_containers(alma_items, aspace_containers, logger)
        )
        self.assertEqual(len(matched_aspace_containers), 0)
        self.assertEqual(len(unmatched_alma_items), 1)
        self.assertEqual(len(unmatched_aspace_containers), 1)
