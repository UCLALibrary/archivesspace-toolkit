import io
import unittest

from asnake import logging
from structlog.testing import capture_logs

from config.base_match import build_match_data, normalize_alma_indicator


def _tc(tc_id: int, indicator: str) -> dict:
    return {"uri": f"/repositories/2/top_containers/{tc_id}", "indicator": indicator}


def _get_indicator(record: dict, logger=None) -> str:
    return record.get("indicator", "")


class TestNormalizeAlmaIndicator(unittest.TestCase):
    """Tests for `normalize_alma_indicator`, shared by all matching profiles."""

    def test_leading_zeroes_removed(self):
        self.assertEqual(normalize_alma_indicator("0011"), "11")

    def test_restricted_removed(self):
        self.assertEqual(normalize_alma_indicator("0011 RESTRICTED"), "11")

    def test_unchanged(self):
        self.assertEqual(normalize_alma_indicator("11P"), "11P")


class TestBuildMatchData(unittest.TestCase):
    """Tests for `build_match_data`, the shared duplicate-key logic."""

    @classmethod
    def setUpClass(cls):
        logging.setup_logging(stream=io.StringIO(), level="INFO")

    def _build(self, records, **kwargs):
        return build_match_data(
            records,
            get_key=_get_indicator,
            record_label="top container",
            id_field="uri",
            **kwargs,
        )

    def test_unique_keys_all_matched(self):
        records = [_tc(1, "1"), _tc(2, "2"), _tc(3, "3")]
        match_data, duplicates = self._build(records)
        self.assertEqual(sorted(match_data.keys()), ["1", "2", "3"])
        self.assertEqual(duplicates, [])

    def test_two_duplicates_excluded(self):
        records = [_tc(1, "1"), _tc(2, "1")]
        match_data, duplicates = self._build(records)
        self.assertEqual(match_data, {})
        self.assertCountEqual(duplicates, records)

    def test_three_duplicates_all_excluded(self):
        records = [_tc(1, "1"), _tc(2, "1"), _tc(3, "1")]
        match_data, duplicates = self._build(records)
        self.assertEqual(match_data, {})
        self.assertCountEqual(duplicates, records)

    def test_duplicates_do_not_affect_other_keys(self):
        records = [_tc(1, "1"), _tc(2, "1"), _tc(3, "1"), _tc(4, "2")]
        match_data, duplicates = self._build(records)
        self.assertEqual(list(match_data.keys()), ["2"])
        self.assertEqual(match_data["2"], records[3])
        self.assertCountEqual(duplicates, records[:3])

    def test_each_duplicate_reported_once(self):
        records = [_tc(1, "1"), _tc(2, "1"), _tc(3, "1")]
        _, duplicates = self._build(records)
        uris = [tc.get("uri") for tc in duplicates]
        self.assertEqual(len(uris), len(set(uris)))

    def test_every_duplicate_is_logged(self):
        records = [_tc(1, "1"), _tc(2, "1"), _tc(3, "1")]
        with capture_logs() as logs:
            self._build(records, logger=logging.get_logger("test"))
        # One message for the first collision, one for the third container.
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(log["log_level"] == "error" for log in logs))
        self.assertIn("/repositories/2/top_containers/3", logs[1]["event"])


if __name__ == "__main__":
    unittest.main()
