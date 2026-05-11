import unittest

from cleanup_compound_indicators_aspace import _parse_compound_indicator


class TestParseCompoundIndicator(unittest.TestCase):
    """Test the `_parse_compound_indicator` function."""

    def setUp(self):
        # Tuples of (input string, expected output list)
        self.valid_test_cases = [
            ("29, 32a, 37, 41, 48", ["29", "32a", "37", "41", "48"]),
            ("1,2,3", ["1", "2", "3"]),
            ("38-42", ["38", "39", "40", "41", "42"]),
            ("1, 3, 3, 5-7", ["1", "3", "5", "6", "7"]),
            ("[1-3]", ["1", "2", "3"]),
            ("[542-543, 554-556 & 762]", ["542", "543", "554", "555", "556", "762"]),
            ("1 & 2, 3-5, 7 and 8", ["1", "2", "3", "4", "5", "7", "8"]),
            ("[1, 3-5], [7-9]", ["1", "3", "4", "5", "7", "8", "9"]),
        ]

        # Only need inputs because we expect a ValueError to be raised
        self.invalid_test_inputs = [
            "38a-42a",
            "Foo-Bar",
            "3-5 and Oversize Box 8",
        ]

    def test_valid_compound_indicators(self):
        for input, expected in self.valid_test_cases:
            with self.subTest(input=input):
                self.assertEqual(_parse_compound_indicator(input), expected)

    def test_invalid_compound_indicators(self):
        for input in self.invalid_test_inputs:
            with self.subTest(input=input):
                # Invalid indicators should raise a ValueError.
                self.assertRaises(ValueError, _parse_compound_indicator, input)
