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


class InjectionMissingReportTest(unittest.TestCase):
    """A /hardness report that found the Opus run had no injection gets meta.injection_missing (the page then opens
    the guide and the card gets a "How to inject" button)."""

    def test_the_real_wording_is_caught(self):
        from server import runner
        for text in ("## Blocking environment defect: the injection was not in the run",
                     "Evidence limits: One run only. The run's environment had none of the injected records, so it did not see the task.",
                     "The hardness review is running on base data only."):
            self.assertTrue(runner.INJECTION_MISSING.search(text), text)

    def test_a_normal_report_is_not_flagged(self):
        from server import runner
        for text in ("The run used the injected Slack post and the relabeled tickets.",
                     "Injection: 01_Universe_Inject.sql, sha256 6f52eda1, revision 17.",
                     "Verdict: PASS. Opus missed the injected PR on ZOM-458."):
            self.assertFalse(runner.INJECTION_MISSING.search(text), text)

    def test_the_page_warns_before_hardness_and_after_such_a_report(self):
        app = (WEB / "app.js").read_text()
        self.assertIn('review[1].toLowerCase() === "hardness"', app)
        self.assertIn('i.key === "injection" && i.state === "saved"', app)
        self.assertIn("m.meta.injection_missing", app)
        common = (WEB / "common.js").read_text()
        self.assertIn("data-injectguide", common)
        self.assertIn("function showInjectGuide(", common)
