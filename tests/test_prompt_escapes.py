"""A prompt copied out of the taxonomy's markdown editor carries backslash escapes the taxonomy doesn't store."""

import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
class PromptEscapeTest(unittest.TestCase):
    def test_prompt_escapes_do_not_count(self):
        from server import fingerprint
        stored = "across #winddown, #admin_foundersonly, #leapblock and the *Q3* sheet [link] total"
        copied = "across #winddown, #admin\\_foundersonly, #leapblock and the \\*Q3\\* sheet \\[link\\] total"
        self.assertEqual(fingerprint.text("prompt", copied), fingerprint.text("prompt", stored))
        self.assertEqual(fingerprint.text("prompt", "C:\\\\path"), fingerprint.text("prompt", "C:\\path"))

    def test_other_fields_keep_backslashes(self):
        from server import fingerprint
        self.assertNotEqual(fingerprint.text("oe", "a\\_b"), fingerprint.text("oe", "a_b"))
        self.assertNotEqual(fingerprint.text("injection", "'a\\_b'"), fingerprint.text("injection", "'a_b'"))

    def test_check_matches_a_copy_from_the_editor(self):
        from server import fingerprint, taxo_check
        stored = "Robert here. Fill the money sheet in #admin_foundersonly and reply with the open total"
        graded = stored.replace("_", "\\_")  # pasted into the portal from the taxonomy editor
        self.assertTrue(taxo_check.compare("prompt", stored, fingerprint.text("prompt", graded), graded)["match"])


if __name__ == "__main__":
    unittest.main()
