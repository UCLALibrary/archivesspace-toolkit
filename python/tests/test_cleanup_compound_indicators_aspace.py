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
        ]

        self.invalid_test_cases = [
            ("38a-42a", ["38a-42a"]),
            ("Foo-Bar", ["Foo-Bar"]),
        ]

    def test_valid_compound_indicators(self):
        for input, expected in self.valid_test_cases:
            with self.subTest(input=input):
                self.assertEqual(_parse_compound_indicator(input), expected)

    def test_invalid_compound_indicators(self):
        for input, _ in self.invalid_test_cases:
            with self.subTest(input=input):
                # Invalid indicators should raise a ValueError.
                self.assertRaises(ValueError, _parse_compound_indicator, input)
