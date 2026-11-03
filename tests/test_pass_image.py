"""The pass image's verification code is exactly the taxonomy's fingerprint line, and the reader check compares it
the way the fingerprint linters do.

taxonomy_v365_fingerprints.json: inputs and what taxonomy v365's fingerprint transformers (271c4e53452d injection,
84a552ee061c prompt, b0a3d62f0196 oe, 870e0b3d907f rubric) returned for them, run with jsonata 2.2.2 (2026-10-27).
"""

import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = json.loads((pathlib.Path(__file__).with_name("taxonomy_v365_fingerprints.json")).read_text())
HAVE_SERVER = importlib.util.find_spec("httpx") is not None


def reader_line(case):
    """The taxonomy's line as its reader is told to copy it: the rubric's weights list left out."""
    return re.sub(r", weights \[[^\]]*\]", "", case["taxonomy"]) if case["stage"] == "rubric" else case["taxonomy"]


@unittest.skipUnless(HAVE_SERVER, "server packages not installed here")
class TaxonomyLineTest(unittest.TestCase):
    def setUp(self):
        from server import fingerprint, taxo_reader
        self.fingerprint, self.taxo_reader = fingerprint, taxo_reader

    def stored(self, case):
        if case["stage"] == "rubric":
            return self.fingerprint.rubric(case["criteria"])
        return self.fingerprint.text(case["stage"], case["text"])

    def test_matches_taxonomy_v365(self):
        for case in CASES:
            with self.subTest(stage=case["stage"], taxonomy=case["taxonomy"]):
                payload = {"stage": case["stage"], "fingerprint": self.stored(case)}
                self.assertEqual(self.taxo_reader.taxonomy_line(payload), reader_line(case))

    def test_old_rubric_chars_and_hardness(self):
        line = self.taxo_reader.taxonomy_line
        self.assertEqual(line({"fingerprint": "3 criteria, Σweight 7, weights [1,3,3] · Titles 120 chars — The agent sends … the end"}),
                         "3 criteria, weight 7 | Titles 120 chars - The  ...  end")
        self.assertEqual(line({"fingerprint": None, "code": "EVP-ABCD-1234-WXYZ"}), "EVP-ABCD-1234-WXYZ")

    def test_rubric_angle_brackets_as_the_builder_saves_them(self):
        """The rubric builder saves a typed <> as &lt;&gt; and keeps <https://...> autolinks (2 of 40 completed
        tasks on 2026-10-27 had "&lt;&gt;" titles); a rubric pasted with plain <> must get the taxonomy's code."""
        rubric = self.fingerprint.rubric
        typed = [{"title": 'Replies on the "Harmony Games <> Ludlow Ventures" email', "weight": 3}]
        saved = [{"title": 'Replies on the "Harmony Games &lt;&gt; Ludlow Ventures" email', "weight": 3}]
        self.assertEqual(rubric(typed), rubric(saved))
        self.assertIn("Titles 47 letters", rubric(saved))  # the taxonomy counts the "lt" and "gt"
        link = [{"title": "The Agent gives <https://prod.example.com/health> as the check", "weight": 1}]
        plain = [{"title": "The Agent gives https://prod.example.com/health as the check", "weight": 1}]
        self.assertEqual(rubric(link), rubric(plain))

    def test_matches_like_the_linters(self):
        m = self.taxo_reader.matches
        self.assertTrue(m("68 chars - Hey ... back", "68 chars - Hey  ... back"))  # whitespace runs
        self.assertTrue(m("68 chars — Hey … back", "68 chars - Hey ... back"))  # dash and ellipsis
        self.assertTrue(m("5 criteria, weight 9, weights [1,2,2,2,2] | Titles 40 letters - TheA ... done",
                          "5 criteria, weight 9 | Titles 40 letters - TheA ... done"))  # a kept weights list
        self.assertFalse(m("57 chars - OE 1 ... 1I1|", "57 chars - OE 1 ... lI1|"))
        self.assertFalse(m("95 chars - A ... 'OO'", "95 chars - -- A ... 'OO'"))

    def test_parse_reply(self):
        reply = '```json\n{"verdict": "PASS", "score": 1, "verification_code": "8 chars - abcd", "reason": "ok"}\n```'
        self.assertEqual(self.taxo_reader._parse(reply)["verification_code"], "8 chars - abcd")
        self.assertEqual(self.taxo_reader._parse("no json here"), {})

    def test_reader_prompts_are_the_taxonomys(self):
        for stage in ("injection", "prompt", "hardness", "oe", "rubric"):
            with self.subTest(stage=stage):
                prompt = self.taxo_reader.PROMPTS[stage]
                self.assertIn(f"pass screen for the /{stage} review", prompt)
                self.assertIn("verification_code", prompt)


@unittest.skipUnless(shutil.which("node") and HAVE_SERVER, "node or server packages not installed here")
class WebShortFpTest(unittest.TestCase):
    def test_pass_card_shows_the_taxonomy_line(self):
        from server import fingerprint
        stored = [fingerprint.rubric(c["criteria"]) if c["stage"] == "rubric" else fingerprint.text(c["stage"], c["text"])
                  for c in CASES]
        script = """
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const shortFp = new Function(src.slice(src.indexOf("const FP_SHOW"), src.indexOf("function rubricWeights")) + "; return shortFp;")();
process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0, "utf8")).map(shortFp)));
"""
        out = subprocess.run(["node", "-e", script, str(ROOT / "web" / "common.js")], input=json.dumps(stored),
                             capture_output=True, text=True, check=True).stdout
        for case, shown in zip(CASES, json.loads(out)):
            with self.subTest(stage=case["stage"], taxonomy=case["taxonomy"]):
                self.assertEqual(shown, reader_line(case))


@unittest.skipUnless(shutil.which("node"), "node not installed here")
class ScreenshotRiskyTest(unittest.TestCase):
    """Codes whose shown characters have brackets, braces or quotes steer to the downloaded image (2026-11-03)."""
    CASES = {
        "3057 chars - INSE ... {}')": True,          # read as "{')" in 12 of 16 screenshots
        "607 chars - Hey  ... .79)": True,
        "1998 chars - INSE ... Z');": True,
        "746 chars - Can  ... ank.": False,          # a period alone reads fine
        "12 chars - abc 'x' def": True,              # short text, shown whole
        "12 criteria, weight 18 | Titles 548 letters - TheA ... nli": False,
        "(empty - no injection)": False,
        "": False,
    }

    def test_flags_brackets_and_quotes(self):
        script = """
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const f = new Function(src.slice(src.indexOf("const RISKY_CHARS"), src.indexOf("function rubricWeights")) + "; return screenshotRisky;")();
process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0, "utf8")).map(f)));
"""
        codes = list(self.CASES)
        out = subprocess.run(["node", "-e", script, str(ROOT / "web" / "common.js")], input=json.dumps(codes),
                             capture_output=True, text=True, check=True).stdout
        for code, got in zip(codes, json.loads(out)):
            with self.subTest(code=code):
                self.assertEqual(got, self.CASES[code])


if __name__ == "__main__":
    unittest.main()
