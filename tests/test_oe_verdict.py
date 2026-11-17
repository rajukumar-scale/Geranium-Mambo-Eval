"""Stage passing verdicts and command aliases (Long Horizon 6 stages)."""

import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
class PassingTest(unittest.TestCase):
    def test_all_lh_stages_pass_on_pass_and_nonfail(self):
        from server import config
        self.assertEqual(config.REVIEW_COMMANDS, ("injection", "prompt", "rubric", "stumping", "all-fail-validation", "reference-notes"))
        for stage in config.REVIEW_COMMANDS:
            with self.subTest(stage=stage):
                self.assertTrue(config.passing(stage, "PASS"))
                self.assertTrue(config.passing(stage, "NON-FAIL"))
                self.assertFalse(config.passing(stage, "FAIL"))

    def test_command_aliases_resolve_to_canonical_stage(self):
        from server import config
        self.assertEqual(config.canonical_command("refnotes"), "reference-notes")
        self.assertEqual(config.canonical_command("all-fail"), "all-fail-validation")
        self.assertEqual(config.canonical_command("allfail"), "all-fail-validation")
        self.assertEqual(config.canonical_command("af"), "all-fail-validation")
        self.assertEqual(config.canonical_command("prompt"), "prompt")


if __name__ == "__main__":
    unittest.main()
