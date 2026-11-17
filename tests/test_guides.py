"""The Guide panel's reading tabs (2026-10-31): GET /api/guides/<name> serves the four guideline files to any signed-in
contributor (fixed allowlist, never a path), the kickoff card links to three of them, and a /hardness report that came
back too easy gets a one-line link to the hardness guide's Part 8. Since 2026-11-04 a tool-call FAIL also links the
tool calls guide and a failure-pattern warning links the failure patterns guide."""

import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, plan

ROOT = Path(__file__).resolve().parent.parent
NAMES = {"hardness": "hardness_v2.md", "toolcalls": "tool_call_minimum.md", "patterns": "distinct_failure_patterns.md",
         "known": "known_data_issues.md"}


class GuideEndpointTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(portal.blocks, "disabled", lambda *a: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
              "values (?,?,?,?,?,?,?)", ("a1", "t1", "u1", "cb@outlier.ai+outlier", "s1", now, now))

    def client(self, signed_in=True):
        client = TestClient(portal.app)
        if signed_in:
            client.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "a1", "user_id": "u1", "iat": time.time()}))
        return client

    def test_the_allowlist_is_the_four_guides(self):
        self.assertEqual(portal.GUIDES, NAMES)
        for file in NAMES.values():
            self.assertTrue((config.GUIDELINES_DIR / file).is_file(), file)

    def test_signed_out_gets_401_for_any_name(self):
        for name in [*NAMES, "nope", "README"]:
            with self.subTest(name=name):
                self.assertEqual(self.client(signed_in=False).get(f"/api/guides/{name}").status_code, 401)

    def test_unknown_names_and_paths_are_404(self):
        c = self.client()
        for name in ("nope", "README", "hardness_v2.md", "hardness_v2", "..%2Fconfig.py", "..%2F..%2F.env", "%2Fetc%2Fpasswd",
                     "Hardness", "known_data_issues"):
            with self.subTest(name=name):
                self.assertEqual(c.get(f"/api/guides/{name}").status_code, 404)

    def test_the_three_guides_are_served_as_markdown(self):
        c = self.client()
        for name, file in NAMES.items():
            with self.subTest(name=name):
                res = c.get(f"/api/guides/{name}")
                self.assertEqual(res.status_code, 200)
                self.assertTrue(res.headers["content-type"].startswith("text/markdown"))
                text = (config.GUIDELINES_DIR / file).read_text(encoding="utf-8")
                self.assertEqual(res.text.splitlines()[0], text.splitlines()[0])
                self.assertNotIn("Built by deploy/", res.text)  # the maintainer note is dropped
                self.assertGreater(len(res.text), 0.9 * len(text))

    def test_a_missing_file_is_404_not_500(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "GUIDELINES_DIR", Path(d)):
            self.assertEqual(self.client().get("/api/guides/hardness").status_code, 404)

    def test_hidden_task_ids_stay_hidden(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "GUIDELINES_DIR", Path(d)):
            (Path(d) / "hardness_v2.md").write_text("# H\n\nTask 6a0000000000000000000001 did this.\n")
            with mock.patch.object(portal.taskids, "known", lambda: frozenset({"6a0000000000000000000001"})):
                res = self.client().get("/api/guides/hardness")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("6a0000000000000000000001", res.text)


class KickoffLinksTest(unittest.TestCase):
    def test_the_card_links_the_guides(self):
        card = plan.card_text()
        self.assertIn("[Read the stumping guide](/?guide=hardness)", card)
        self.assertIn("[Read the horizon guide](/?guide=toolcalls)", card)
        self.assertIn("[Read the rubric & all-fail guide](/?guide=patterns)", card)
        for name in re.findall(r"\]\(/\?guide=(\w+)", card):
            self.assertIn(name, portal.GUIDES)


@unittest.skipUnless(shutil.which("node"), "node not installed here")
class WebGuideLinksTest(unittest.TestCase):
    """common.js in node with a bare DOM stub: the too-easy condition, the hint in the report card, and the heading slugs."""

    SCRIPT = """
const fs = require("fs");
global.window = {}; global.location = { origin: "http://x", href: "http://x/" };
global.localStorage = { getItem() { return null; }, setItem() {} };
global.document = { querySelectorAll: () => [], documentElement: { dataset: {} } };
const src = fs.readFileSync(process.argv[1], "utf8");
const lib = new Function(src + "; return { hardnessTooEasy, messageHtml, guideSlug, TOO_EASY_LINK, TOOLCALLS_LINK, PATTERNS_LINK };")();
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({
  tooEasy: input.metas.map(lib.hardnessTooEasy),
  html: input.metas.map(meta => lib.messageHtml({ id: 1, role: "report", content: "r", created_at: 1, meta })),
  adminHtml: lib.messageHtml({ id: 1, role: "report", content: "r", created_at: 1, meta: input.metas[0] }, { admin: true }),
  slugs: input.headings.map(lib.guideSlug),
  link: lib.TOO_EASY_LINK,
  reportHtml: input.reports.map(r => lib.messageHtml({ id: 1, role: "report", content: r.content, created_at: 1, meta: r.meta })),
  adminReportHtml: lib.messageHtml({ id: 1, role: "report", content: input.reports[0].content, created_at: 1,
                                     meta: input.reports[0].meta }, { admin: true }),
  toolcallsLink: lib.TOOLCALLS_LINK, patternsLink: lib.PATTERNS_LINK,
}));
"""
    METAS = [
        ({"stage": "hardness", "verdict": "FAIL", "score": 1}, True),
        ({"stage": "hardness", "verdict": "FAIL", "score": 2}, True),
        ({"stage": "hardness", "verdict": "FAIL", "score": None}, True),
        ({"stage": "hardness", "verdict": None, "score": 2}, True),  # the report says Easy even if no verdict parsed
        ({"stage": "hardness", "verdict": "NON-FAIL", "score": 3}, False),
        ({"stage": "hardness", "verdict": "PASS", "score": 5}, False),
        ({"stage": "hardness", "verdict": "NON-FAIL", "score": None}, False),  # unscored provisional
        ({"stage": "prompt", "verdict": "FAIL", "score": 2}, False),
        ({"stage": "rubric", "verdict": "FAIL", "score": 1}, False),
    ]

    # (meta, report text, tool-call hint?, failure-pattern hint?): the text lines are copied from live /hardness reports.
    FAIL2 = {"stage": "hardness", "verdict": "FAIL", "score": 2}
    PASS5 = {"stage": "hardness", "verdict": "PASS", "score": 5}
    REPORTS = [
        (FAIL2, "**Why Easy (tool calls):** this run made 61 tool calls; a Long-Horizon task needs 100", True, False),
        (FAIL2, "**Why Easy (tool calls):** this run made 61 tool calls\n"
                "⚠ **Failure-pattern risk:** Calvin's comment decides about 40% of the task's answer", True, True),
        (PASS5, "Failure-pattern risk: **YES** (advice only; doesn't change the score).", False, True),
        (PASS5, "- **Failure-pattern risk: YES (advice only).** Traps and this run's result", False, True),
        (PASS5, "**Failure-pattern risk: YES** (advice only; it doesn't change the score).", False, True),
        (PASS5, "- **Failure-pattern risk: NO.** Traps, each with its estimated share", False, False),
        (PASS5, "Failure-pattern risk: NO. Largest trap share is about 25% (below 30%).\n"
                "Tool calls: 142 (Long-Horizon, minimum 100; not under 90% of the minimum).", False, False),
        ({"stage": "hardness", "verdict": "NON-FAIL", "score": None},
         "Failure-pattern risk: N/A (unscored; traps not assessed).", False, False),
        ({**FAIL2, "injection_missing": True}, "**Why Easy (tool calls):** 61\n⚠ **Failure-pattern risk:** x", False, False),
        ({"stage": "prompt", "verdict": "FAIL", "score": 2}, "**Why Easy (tool calls):** ⚠ Failure-pattern risk", False, False),
    ]

    @classmethod
    def setUpClass(cls):
        cls.hardness = (config.GUIDELINES_DIR / "hardness_v2.md").read_text(encoding="utf-8")
        cls.headings = re.findall(r"^#{1,3} (.+)$", cls.hardness, re.M)
        cls.guide_headings = {name: re.findall(r"^#{1,3} (.+)$", (config.GUIDELINES_DIR / file).read_text(encoding="utf-8"), re.M)
                              for name, file in NAMES.items()}
        heads = cls.headings + [h for name in ("toolcalls", "patterns") for h in cls.guide_headings[name]]
        reports = [{"meta": m, "content": c} for m, c, _, _ in cls.REPORTS]
        out = subprocess.run(["node", "-e", cls.SCRIPT, str(ROOT / "web" / "common.js")],
                             input=json.dumps({"metas": [m for m, _ in cls.METAS], "headings": heads, "reports": reports}),
                             capture_output=True, text=True, check=True).stdout
        cls.out = json.loads(out)

    def test_too_easy_condition(self):
        for (meta, want), got in zip(self.METAS, self.out["tooEasy"]):
            with self.subTest(meta=meta):
                self.assertIs(got, want)

    def test_the_hint_shows_under_too_easy_reports_only(self):
        for (meta, want), html in zip(self.METAS, self.out["html"]):
            with self.subTest(meta=meta):
                self.assertEqual("Too easy? See the" in html, want)
        self.assertIn(f'href="{self.out["link"]}"', self.out["html"][0])
        self.assertIn("hardness guide, Part 8. If the run comes back too easy</a>", self.out["html"][0])
        self.assertNotIn("Too easy?", self.out["adminHtml"])  # the /admin page has no Guide panel to open

    def test_tool_call_and_failure_pattern_hints(self):
        for (meta, text, tool, pattern), html in zip(self.REPORTS, self.out["reportHtml"]):
            with self.subTest(text=text[:60], meta=meta):
                self.assertEqual("Too few tool calls? See the" in html, tool)
                self.assertEqual(f'href="{self.out["toolcallsLink"]}"' in html, tool)
                self.assertEqual("Failure-pattern risk? See the" in html, pattern)
                self.assertEqual(f'href="{self.out["patternsLink"]}"' in html, pattern)
        first = self.out["reportHtml"][1]  # a tool-call FAIL with the warning: tool calls, then too easy, then the pattern
        self.assertLess(first.index("Too few tool calls?"), first.index("Too easy?"))
        self.assertLess(first.index("Too easy?"), first.index("Failure-pattern risk?"))
        self.assertNotIn("guide-hint", self.out["adminReportHtml"])

    def test_the_tool_call_and_pattern_links_hit_real_headings(self):
        slugs = dict(zip(self.out["slugs"], self.headings + [h for n in ("toolcalls", "patterns") for h in self.guide_headings[n]]))
        for link, tab, heading in ((self.out["toolcallsLink"], "toolcalls", "Part 4. What adds real calls (do these)"),
                                   (self.out["patternsLink"], "patterns", "4. Build at least three traps that split the rollouts")):
            with self.subTest(link=link):
                self.assertTrue(link.startswith(f"/?guide={tab}#"))
                self.assertIn(tab, portal.GUIDES)
                self.assertEqual(slugs.get(link.split("#", 1)[1]), heading)
                self.assertIn(heading, self.guide_headings[tab])

    def test_the_hint_links_to_part_8_by_its_slug(self):
        slugs = dict(zip(self.out["slugs"], self.headings))
        anchor = self.out["link"].split("#", 1)[1]
        self.assertTrue(self.out["link"].startswith("/?guide=hardness#"))
        self.assertEqual(slugs.get(anchor), "Part 8. If the run comes back too easy")

    def test_slugs_are_github_style(self):
        slugs = dict(zip(self.headings, self.out["slugs"]))
        self.assertEqual(slugs["Appendix. One-page checklist"], "appendix-one-page-checklist")
        self.assertEqual(slugs['1. Proving "done" across services'], "1-proving-done-across-services")


class WebGuidePanelTest(unittest.TestCase):
    """The panel's wiring in app.js (static checks: the panel itself needs a browser)."""

    def setUp(self):
        self.app = (ROOT / "web" / "app.js").read_text()

    def test_five_tabs_steps_first(self):
        tabs = re.search(r"const GUIDE_TABS = (\[.*?\]);\n", self.app).group(1)
        self.assertEqual(re.findall(r'\["(\w+)"', tabs), ["steps", "hardness", "toolcalls", "patterns", "known"])
        self.assertEqual(set(re.findall(r'\["(\w+)"', tabs)[1:]), set(portal.GUIDES))

    def test_last_tab_is_remembered_and_deep_links_open(self):
        self.assertIn('localStorage.setItem("evp_guide_tab"', self.app)
        self.assertIn("openGuideFromUrl();", self.app)


if __name__ == "__main__":
    unittest.main()
