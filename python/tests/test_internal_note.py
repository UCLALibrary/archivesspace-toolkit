import unittest

from migrate_alma_metadata_to_archivesspace import (
    _append_internal_note,
    _build_internal_note,
)


def _note(timestamp: str) -> str:
    """Migration note in the format written by _append_internal_note."""
    return (
        "Metadata updated in a scripted batch migration "
        f"from Alma item record on {timestamp}"
    )


NOTE_1 = _note("2026-08-01T10:00:00Z")
NOTE_2 = _note("2026-09-01T10:00:00Z")
NOTE_3 = _note("2026-10-20T09:14:03Z")


class TestBuildInternalNote(unittest.TestCase):
    """Tests for `_build_internal_note`."""

    def test_empty_existing_note(self):
        self.assertEqual(_build_internal_note("", NOTE_3), NOTE_3)

    def test_appends_when_within_limit(self):
        existing = f"Staff note\n{NOTE_1}"
        self.assertEqual(
            _build_internal_note(existing, NOTE_2), f"{existing}\n{NOTE_2}"
        )


    def test_third_migration_replaces_oldest(self):
        # The case from the ticket: two prior migrations, third run would exceed limit.
        existing = f"{NOTE_1}\n{NOTE_2}"
        self.assertGreater(len(f"{existing}\n{NOTE_3}"), 255)
        self.assertEqual(_build_internal_note(existing, NOTE_3), f"{NOTE_2}\n{NOTE_3}")

    def test_only_as_many_migration_lines_removed_as_needed(self):
        result = _build_internal_note(f"{NOTE_1}\n{NOTE_2}", NOTE_3, max_length=200)
        self.assertEqual(result, f"{NOTE_2}\n{NOTE_3}")
        result = _build_internal_note(f"{NOTE_1}\n{NOTE_2}", NOTE_3, max_length=100)
        self.assertEqual(result, NOTE_3)

    def test_other_lines_are_kept(self):
        existing = f"Staff note A\n{NOTE_1}\nStaff note B\n{NOTE_2}"
        result = _build_internal_note(existing, NOTE_3)
        self.assertEqual(result, f"Staff note A\nStaff note B\n{NOTE_2}\n{NOTE_3}")
        self.assertLessEqual(len(result), 255)

    def test_returns_none_when_other_content_too_long(self):
        existing = "foo" * 200
        self.assertIsNone(_build_internal_note(existing, NOTE_3))

    def test_result_never_exceeds_limit(self):
        for existing in [
            "",
            NOTE_1,
            f"{NOTE_1}\n{NOTE_2}",
            f"{NOTE_1}\n{NOTE_2}\n{NOTE_1}",
            f"{'foo' * 10}\n{NOTE_1}\n{NOTE_2}",
        ]:
            with self.subTest(existing=existing):
                result = _build_internal_note(existing, NOTE_3)
                self.assertIsNotNone(result)
                self.assertLessEqual(len(result), 255)
                self.assertTrue(result.endswith(NOTE_3))


class TestAppendInternalNote(unittest.TestCase):
    """Tests for `_append_internal_note`, which mutates the top container dict."""

    TIMESTAMP = "2026-10-20T09:14:03Z"

    def test_missing_or_null_note(self):
        for tc in [{"uri": "/tc/1"}, {"uri": "/tc/1", "internal_note": None}]:
            with self.subTest(tc=tc):
                _append_internal_note(tc, self.TIMESTAMP)
                self.assertEqual(tc["internal_note"], NOTE_3)

    def test_replaces_old_notes_on_third_run(self):
        tc = {"uri": "/tc/1", "internal_note": f"{NOTE_1}\n{NOTE_2}"}
        _append_internal_note(tc, self.TIMESTAMP)
        self.assertEqual(tc["internal_note"], f"{NOTE_2}\n{NOTE_3}")

    def test_leaves_note_unchanged_when_it_cannot_fit(self):
        original = "z" * 250
        tc = {"uri": "/tc/1", "internal_note": original}
        _append_internal_note(tc, self.TIMESTAMP)
        self.assertEqual(tc["internal_note"], original)