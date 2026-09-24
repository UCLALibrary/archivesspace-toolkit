import unittest
from utils.aspace_utils import get_false_duplicate_reason


class TestFalseDuplicateReason(unittest.TestCase):
    """Tests for `get_false_duplicate_reason`, using titles from UCLA data."""

    def test_accession_whole_word_any_position(self):
        for title in [
            "Accession LSC-2021-005",  # beginning
            "Papers from accession LSC-2019-12, unprocessed",  # middle
            "Unprocessed papers, ACCESSION",  # end, capitalized
            "Accession",
        ]:
            with self.subTest(title=title):
                reason = get_false_duplicate_reason([("Parent AO", title)])
                self.assertEqual(
                    reason, f"Parent AO title contains 'accession': {title}"
                )

    def test_accession_variants_do_not_match(self):
        for title in [
            "Commercial AV/BD - to be deaccessioned",
            "Deaccession records",
            '"Library Accessions 3,000,000th Book, Opens New Wing, Acquires '
            'Collections" in <title>UCLA Weekly</title><lb/>Pages 1, 4',
            "Accessioned 2019",
            "Accessioning files",
        ]:
            with self.subTest(title=title):
                self.assertIsNone(get_false_duplicate_reason([("Linked AO", title)]))

    def test_backlog(self):
        for title in [
            "Accession LSC-9999-132 backlog material",
            "Backlog",
            "BACKLOG MATERIAL",
        ]:
            with self.subTest(title=title):
                self.assertIsNotNone(get_false_duplicate_reason([("Linked AO", title)]))

    def test_not_false_duplicate(self):
        for title in ["Commercial AV", "400 production stills, 8 x 10.", "", None]:
            with self.subTest(title=title):
                self.assertIsNone(get_false_duplicate_reason([("Linked AO", title)]))

    def test_reason_names_source_and_title(self):
        reason = get_false_duplicate_reason(
            [
                ("Linked AO", "Commercial AV/BD - to be deaccessioned"),
                ("Parent AO", "Accession LSC-2021-005"),
            ]
        )
        self.assertEqual(
            reason, "Parent AO title contains 'accession': Accession LSC-2021-005"
        )


if __name__ == "__main__":
    unittest.main()
