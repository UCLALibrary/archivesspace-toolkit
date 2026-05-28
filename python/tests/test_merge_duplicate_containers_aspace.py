import unittest

from merge_duplicate_containers_aspace import _determine_canonical_tc


class TestMergeDuplicateContainers(unittest.TestCase):
    """Tests for the `merge_duplicate_containers_aspace` module."""

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
                "ao_refs": [
                    "/archival_objects/1",
                    "/archival_objects/2",
                    "/archival_objects/3",
                ],
                "create_time": "2026-01-03T00:00:00Z",  # most recent
            },
            {
                "uri": "/top_containers/2",
                "type": "box",
                "indicator": "1",
                "ao_refs": ["/archival_objects/4", "/archival_objects/5"],
                "create_time": "2026-01-02T00:00:00Z",
            },
            {
                "uri": "/top_containers/3",
                "type": "box",
                "indicator": "1",
                "ao_refs": ["/archival_objects/6"],
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
                "ao_refs": [
                    "/archival_objects/1",
                    "/archival_objects/2",
                ],
                "create_time": "2026-01-03T00:00:00Z",
            },
            {
                "uri": "/top_containers/2",
                "type": "box",
                "indicator": "1",
                "ao_refs": ["/archival_objects/4", "/archival_objects/5"],
                "create_time": "2026-01-01T00:00:00Z",  # oldest
            },
            {
                "uri": "/top_containers/3",
                "type": "box",
                "indicator": "1",
                "ao_refs": ["/archival_objects/6"],
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
                "ao_refs": [
                    "/archival_objects/1",
                    "/archival_objects/2",
                ],
                "create_time": "2026-01-03T00:00:00Z",
            },
            {
                "uri": "/top_containers/2",
                "type": "box",
                "indicator": "1",
                "ao_refs": ["/archival_objects/4", "/archival_objects/5"],
                # missing create time
            },
            {
                "uri": "/top_containers/3",
                "type": "box",
                "indicator": "1",
                "ao_refs": ["/archival_objects/6"],
                "create_time": "2026-01-02T00:00:00Z",
            },
        ]
        canonical, duplicate_tcs = _determine_canonical_tc(test_tcs)
        self.assertEqual(canonical, test_tcs[0])
        self.assertEqual(duplicate_tcs, test_tcs[1:])
