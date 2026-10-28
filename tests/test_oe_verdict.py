"""An OE NON-FAIL counts as a fail: the taxonomy's OE verdict only takes PASS (2026-10-27)."""

import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
class PassingTest(unittest.TestCase):
    def test_oe_needs_pass(self):
        from server import config
        self.assertTrue(config.passing("oe", "PASS"))
        self.assertFalse(config.passing("oe", "NON-FAIL"))
        self.assertFalse(config.passing("oe", "FAIL"))

    def test_other_stages_still_pass_on_nonfail(self):
        from server import config
        for stage in ("injection", "prompt", "hardness", "rubric"):
            with self.subTest(stage=stage):
                self.assertTrue(config.passing(stage, "PASS"))
                self.assertTrue(config.passing(stage, "NON-FAIL"))
                self.assertFalse(config.passing(stage, "FAIL"))


if __name__ == "__main__":
    unittest.main()
