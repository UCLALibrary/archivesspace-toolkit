import unittest

from asnake import logging
from structlog.testing import capture_logs
from types import ModuleType

from config import (
    indicator_only_matching,
    indicator_type_matching,
    series_description_matching,
)
from config.base_match import UnparseableAlmaKey, match_containers


def _item(pid: str, description) -> dict:
    return {"pid": pid, "barcode": f"BC{pid}", "description": description}


# Thanks to https://adamj.eu/tech/2025/05/30/python-unittest-common-tests/
class Common:
    class NonstandardDescriptionTests(unittest.TestCase):
        """Shared tests, run once per matching profile (see subclasses below).

        Alma items with descriptions a profile can't parse should be skipped and
        reported as unmatched, not crash the run or block other items from matching.
        """

        profile: ModuleType
        # A standard Alma description and the ASpace top container it should match.
        good_description = ""
        good_tc: dict = {}
        # Descriptions this profile can't parse.
        bad_descriptions: list = []

        def _match(self, alma_items: list[dict], logger=None):
            alma_match_data, items_with_duplicate_keys = (
                self.profile.get_alma_match_data(alma_items, logger)
            )
            aspace_match_data, _ = self.profile.get_aspace_match_data(
                [dict(self.good_tc)], logger
            )
            matched, unhandled_data = match_containers(
                alma_match_data, aspace_match_data
            )
            return matched, unhandled_data, items_with_duplicate_keys

        def test_nonstandard_descriptions_reported_as_unmatched(self):
            for description in self.bad_descriptions:
                with self.subTest(description=description):
                    bad_item = _item("2", description)
                    matched, unhandled_data, duplicates = self._match(
                        [_item("1", self.good_description), bad_item]
                    )
                    # The standard item still matches...
                    self.assertEqual(len(matched), 1)
                    self.assertEqual(matched[0]["barcode"], "BC1")
                    # ...and the nonstandard one is reported as unmatched, unchanged.
                    self.assertEqual(unhandled_data["unmatched_alma_items"], [bad_item])
                    self.assertEqual(duplicates, [])

        def test_nonstandard_descriptions_not_reported_as_duplicates(self):
            # Two items with the same unparseable description aren't duplicates
            # of each other; both are just unmatched.
            items = [
                _item("1", "sx142967-sx143045"),
                _item("2", "sx142967-sx143045"),
            ]
            matched, unhandled_data, duplicates = self._match(items)
            self.assertEqual(matched, [])
            self.assertEqual(duplicates, [])
            self.assertEqual(len(unhandled_data["unmatched_alma_items"]), 2)

        def test_nonstandard_description_logged(self):
            with capture_logs() as logs:
                self._match(
                    [_item("2", "sx142967-sx143045")], logger=logging.get_logger("t")
                )
            errors = [log["event"] for log in logs if log["log_level"] == "error"]
            self.assertEqual(len(errors), 1)
            self.assertIn("Alma item 2", errors[0])
            self.assertIn("BC2", errors[0])
            self.assertIn("sx142967-sx143045", errors[0])

        def test_key_is_unparseable_sentinel(self):
            key = self.profile._get_alma_key(_item("2", "sx142967-sx143045"))
            self.assertIsInstance(key, UnparseableAlmaKey)


class TestIndicatorOnly(Common.NonstandardDescriptionTests):
    profile = indicator_only_matching
    good_description = "box.0023"
    good_tc = {"uri": "/repositories/2/top_containers/1", "indicator": "23"}
    bad_descriptions = ["sx142967-sx143045", "box.", ""]


class TestIndicatorType(Common.NonstandardDescriptionTests):
    profile = indicator_type_matching
    good_description = "box.0023"
    good_tc = {
        "uri": "/repositories/2/top_containers/1",
        "indicator": "23",
        "type": "box",
    }
    bad_descriptions = ["sx142967-sx143045", "box.", ".0023", ""]


class TestSeriesDescription(Common.NonstandardDescriptionTests):
    profile = series_description_matching
    good_description = "ser.P box.0011"
    good_tc = {
        "uri": "/repositories/2/top_containers/1",
        "indicator": "11P",
        "type": "box",
    }
    bad_descriptions = [
        "sx142967-sx143045",
        "box.0011",  # no series
        "ser.P",  # no container
        "ser.P box",  # no indicator
        "ser. box.0011",  # empty series
        "",
    ]
