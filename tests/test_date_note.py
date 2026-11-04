"""Every chat reply is told the task date as saved right now (2026-11-03: a date set with the portal's picker never
reached the resumed chat session, and the assistant kept asking for it from memory)."""

import unittest
from unittest import mock

from server import runner, workspace


class DateNoteTest(unittest.TestCase):
    def test_a_saved_date_is_stated(self):
        with mock.patch.object(workspace, "dated_release", lambda: True), \
                mock.patch.object(workspace, "universe_date", lambda a: "synthcut"):
            note = runner.date_note("a1")
        self.assertIn("Oct 1, 2025 (synth-cut)", note)
        self.assertIn("don't ask for it again", note)

    def test_nothing_when_unsaved_or_single_universe_or_broken(self):
        with mock.patch.object(workspace, "dated_release", lambda: True), mock.patch.object(workspace, "universe_date", lambda a: None):
            self.assertEqual(runner.date_note("a1"), "")
        with mock.patch.object(workspace, "dated_release", lambda: False):
            self.assertEqual(runner.date_note("a1"), "")
        with mock.patch.object(workspace, "dated_release", lambda: True), \
                mock.patch.object(workspace, "universe_date", mock.Mock(side_effect=OSError("gone"))):
            self.assertEqual(runner.date_note("a1"), "")


if __name__ == "__main__":
    unittest.main()
