import io
import unittest

from asnake import logging
from merge_duplicate_containers_aspace import (
    _determine_canonical_tc,
    _has_location_data,
    _has_recent_accession_keywords,
)

# Structlog comes with a context manager for capturing logs
# before they hit the logging processors, making testing cleaner and easier.
# See docs @https://www.structlog.org/en/stable/testing.html
capture_logs = logging.structlog.testing.capture_logs


class TestMergeDuplicateContainers(unittest.TestCase):
    """Tests for the `merge_duplicate_containers_aspace` module."""

    @classmethod
    def setUpClass(cls):
        # Log to in-memory buffer so no output to console or file
        logging.setup_logging(stream=io.StringIO(), level="INFO")

    def test_determine_canonical_tc_ao_count(self):
        """Test that the canonical TC is the one with the most archival objects."""
        # The first TC has 3 archival object refs,
        # so it should be the canonical TC,
        # even though it's the most recent.
        test_tcs = [
            {
                "uri": "/top_containers/1",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/1",
                        "title": "Archival Object 1",
                    },
                    {
                        "uri": "/archival_objects/2",
                        "title": "Archival Object 2",
                    },
                    {
                        "uri": "/archival_objects/3",
                        "title": "Archival Object 3",
                    },
                ],
                "create_time": "2026-01-03T00:00:00Z",  # most recent
            },
            {
                "uri": "/top_containers/2",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/4",
                        "title": "Archival Object 4",
                    },
                    {
                        "uri": "/archival_objects/5",
                        "title": "Archival Object 5",
                    },
                ],
                "create_time": "2026-01-02T00:00:00Z",
            },
            {
                "uri": "/top_containers/3",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/6",
                        "title": "Archival Object 6",
                    },
                ],
                "create_time": "2026-01-01T00:00:00Z",
            },
        ]
        canonical, duplicate_tcs = _determine_canonical_tc(test_tcs)
        self.assertEqual(canonical, test_tcs[0])
        self.assertEqual(duplicate_tcs, test_tcs[1:])

    def test_determine_canonical_tc_create_time(self):
        """Test that the canonical TC is the one with the oldest create time,
        when there are ties in the number of archival objects.
        """
        # The first and second TCs have the same number of archival objects,
        # but the second TC has the oldest create time,
        # so it should be the canonical TC.
        test_tcs = [
            {
                "uri": "/top_containers/1",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/1",
                        "title": "Archival Object 1",
                    },
                    {
                        "uri": "/archival_objects/2",
                        "title": "Archival Object 2",
                    },
                ],
                "create_time": "2026-01-03T00:00:00Z",
            },
            {
                "uri": "/top_containers/2",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/4",
                        "title": "Archival Object 4",
                    },
                    {
                        "uri": "/archival_objects/5",
                        "title": "Archival Object 5",
                    },
                ],
                "create_time": "2026-01-01T00:00:00Z",  # oldest
            },
            {
                "uri": "/top_containers/3",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/6",
                        "title": "Archival Object 6",
                    },
                ],
                "create_time": "2026-01-02T00:00:00Z",
            },
        ]
        canonical, duplicate_tcs = _determine_canonical_tc(test_tcs)
        self.assertEqual(canonical, test_tcs[1])
        # Order should be 1, 0, 2 in this case
        self.assertEqual(duplicate_tcs, [test_tcs[0], test_tcs[2]])

    def test_determine_canonical_tc_missing_create_time(self):
        """Test that the canonical TC is the one with the oldest create time,
        when there are ties in the number of archival objects and create time is missing.
        """
        # The first and second TCs have the same number of archival objects,
        # but the second TC is missing a create time,
        # so the first should be the canonical TC.
        test_tcs = [
            {
                "uri": "/top_containers/1",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/1",
                        "title": "Archival Object 1",
                    },
                    {
                        "uri": "/archival_objects/2",
                        "title": "Archival Object 2",
                    },
                ],
                "create_time": "2026-01-03T00:00:00Z",
            },
            {
                "uri": "/top_containers/2",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/4",
                        "title": "Archival Object 4",
                    },
                    {
                        "uri": "/archival_objects/5",
                        "title": "Archival Object 5",
                    },
                ],
                # missing create time
            },
            {
                "uri": "/top_containers/3",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/6",
                        "title": "Archival Object 6",
                    },
                ],
                "create_time": "2026-01-02T00:00:00Z",
            },
        ]
        canonical, duplicate_tcs = _determine_canonical_tc(test_tcs)
        self.assertEqual(canonical, test_tcs[0])
        self.assertEqual(duplicate_tcs, test_tcs[1:])

    def test_check_for_location_data(self):
        """Test that location data is flagged for review in logs if found."""
        test_tcs = [
            {
                "uri": "/top_containers/1",
                "type": "box",
                "indicator": "1",
                "container_locations": ["location1", "location2"],
            },
            {
                "uri": "/top_containers/2",
                "type": "box",
                "indicator": "1",
                "container_locations": [],
            },
        ]

        with capture_logs() as logs:
            result = _has_location_data(test_tcs)

        # Function should return True and log a warning message
        self.assertTrue(result)
        # There should only be one log message,
        # since the second TC shouldn't generate one
        # because it has no location data.
        self.assertEqual(len(logs), 1)
        self.assertEqual(
            logs[0]["event"],
            "Top container /top_containers/1 has location data: ['location1', 'location2']",
        )
        self.assertEqual(logs[0]["log_level"], "warning")

    def test_has_recent_accession_keywords(self):
        """Test that `_has_recent_accession_keywords` returns True
        and logs a warning message if recent accession keywords are found."""
        test_tcs = [
            {
                "uri": "/top_containers/1",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/1",
                        "title": "Accession 1",
                    },
                    {
                        "uri": "/archival_objects/2",
                        "title": "Backlog 2",
                    },
                ],
            },
        ]

        with capture_logs() as logs:
            result = _has_recent_accession_keywords(test_tcs)

        # Function should return True and log a warning message
        self.assertTrue(result)
        self.assertEqual(len(logs), 1)
        self.assertEqual(
            logs[0]["event"],
            "Manual review required",
        )
        self.assertEqual(logs[0]["log_level"], "warning")

    def test_has_recent_accession_keywords_no_keywords(self):
        """Test that `_has_recent_accession_keywords` returns False
        if no recent accession keywords are found."""
        test_tcs = [
            {
                "uri": "/top_containers/1",
                "type": "box",
                "indicator": "1",
                "_related_aos_temp": [
                    {
                        "uri": "/archival_objects/1",
                        "title": "Archival Object 1",  # no recent accession keywords
                    },
                ],
            },
        ]

        with capture_logs() as logs:
            result = _has_recent_accession_keywords(test_tcs)

        # Function should return False and log no messages
        self.assertFalse(result)
        self.assertEqual(len(logs), 0)
