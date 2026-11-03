"""The pass screen of a /prompt or /hardness pass reminds the contributor to run their injection SQL in every sandbox
(Universe Explorer, Agent Runner; not the Rubric Verifier) before showing the pass (Evan, 2026-11-03)."""

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class InjectReminderTest(unittest.TestCase):
    def test_prompt_and_hardness_passes_get_the_sandbox_injection_step(self):
        js = (WEB / "common.js").read_text()
        stages = re.search(r'const INJECT_REMINDER_STAGES = new Set\(\[([^\]]*)\]\)', js).group(1)
        self.assertEqual(sorted(re.findall(r'"(\w+)"', stages)), ["hardness", "prompt"])
        self.assertIn("show.onclick = INJECT_REMINDER_STAGES.has(p.stage) ? injectStep : card", js)
        for words in ("Universe Explorer", "Agent Runner", "Not needed in the Rubric Verifier", "_changelog", "No records found"):
            self.assertIn(words, js)

    def test_its_screenshots_are_shipped(self):
        js = (WEB / "common.js").read_text()
        for name in re.findall(r'"(inject_\d_\w+\.png)"', js):
            self.assertTrue((WEB / "img" / name).is_file(), name)
        self.assertEqual(len(re.findall(r'"(inject_\d_\w+\.png)"', js)), 4)


if __name__ == "__main__":
    unittest.main()
