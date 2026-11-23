"""The pass code's "check NNNNN" (BKL-15, 2026-11-23): a 5-digit checksum over the whole compared text, so a code
can't be typed in from the first/last 4 characters alone. The linters compute the same number in JSONata; the
expected values here are what jsonata-js 2.0.6 returns for the linters' $ck."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, fingerprint, proofs, workspace


class ChecksumTest(unittest.TestCase):
    def test_matches_the_linters(self):
        self.assertEqual(fingerprint.checksum("a"), "00066")
        self.assertEqual(fingerprint.checksum("Draft the reply to Priya"), "52758")
        # Emoji is two UTF-16 code units in JSONata's $split; smart quotes and dashes count as 96.
        self.assertEqual(fingerprint.checksum("Café 😀 — “quoted”"), "46699")
        self.assertEqual(fingerprint.checksum("5,3,1|TheAgentemails"), "77894")

    def test_one_changed_character_changes_the_check(self):
        a = "Flag rows where A < B in payout_transfer_watch.py"
        self.assertNotEqual(fingerprint.checksum(a), fingerprint.checksum(a.replace("A < B", "A > B")))

    def test_text_stages_use_the_fingerprints_normalized_text(self):
        raw = "  Draft the reply\n\n to Priya.  "
        self.assertEqual(fingerprint.check_of_input("prompt", raw), fingerprint.checksum("Draft the reply to Priya"))
        self.assertEqual(fingerprint.check_of_input("injection", raw), fingerprint.check_of_input("injection", "Draft the reply to Priya"))

    def test_rubric_uses_weights_and_title_letters(self):
        criteria = [{"title": "The Agent emails priya@x.com.", "weight": 5}, {"title": "No posts in #infra", "weight": "3"}]
        self.assertEqual(fingerprint.check_of_input("rubric", json.dumps(criteria)),
                         fingerprint.checksum("5,3|TheAgentemailspriyaxcomNopostsininfra"))
        self.assertEqual(fingerprint.check_of_input("rubric", json.dumps({"criteria": criteria})),
                         fingerprint.check_of_input("rubric", json.dumps(criteria)))

    def test_no_check_without_input(self):
        self.assertIsNone(fingerprint.check_of_input("prompt", "   "))
        self.assertIsNone(fingerprint.check_of_input("rubric", ""))
        self.assertIsNone(fingerprint.check_of_input("rubric", "not json"))
        self.assertIsNone(fingerprint.check_of_input("runs-base", "x"))


class ProofCheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.saved = {"prompt": "Draft the reply to Priya with the $1,234.56 figure."}
        for p in [mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.object(workspace, "read_input",
                                    lambda a, key, limit=0: {"files": [{"text": self.saved[key]}]} if key in self.saved else None)]:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()

    def proof(self, fp):
        run = {"id": "r1", "attempt_id": "a1", "command": "prompt", "models": {}, "started_at": time.time() - 60,
               "finished_at": time.time(), "cost_estimate": 1.0}
        report = {"stage": "prompt", "verdict": "PASS", "score": 5, "exec_status": "COMPLETE", "file": "prompt.md",
                  "sha256": "ab" * 32, "fingerprint": fp}
        return proofs.create(run, {"task_id": "t1", "user_id": "u1", "email": "a1@x.com"}, report)

    def test_check_of_the_graded_input(self):
        p = self.proof(fingerprint.of_input("prompt", self.saved["prompt"]))
        self.assertEqual(p["fingerprint_check"], fingerprint.check_of_input("prompt", self.saved["prompt"]))

    def test_no_check_when_the_input_changed_since(self):
        p = self.proof(fingerprint.of_input("prompt", "An older prompt."))
        self.assertIsNone(p["fingerprint_check"])

    def test_no_check_without_a_fingerprint(self):
        self.assertNotIn("fingerprint_check", self.proof(None))


class ReaderCheckTest(unittest.TestCase):
    PAYLOAD = {"stage": "prompt", "verdict": "PASS", "fingerprint": fingerprint.text("prompt", "Draft the reply to Priya now"),
               "fingerprint_check": "52758"}

    BASE = "28 chars - Draf ...  now"

    def read_back(self, code):
        from server import taxo_reader
        with mock.patch.object(taxo_reader, "read", mock.AsyncMock(return_value={"verification_code": code, "verdict": "PASS"})):
            return asyncio.run(taxo_reader.check(dict(self.PAYLOAD), b"png"))

    def test_the_whole_code_reads_back(self):
        result = self.read_back(self.BASE + " check 52758")
        self.assertTrue(result["ok"])
        self.assertEqual(result["expected"], self.BASE + " check 52758")
        self.assertTrue(self.read_back("28 chars – Draf …  now Check52758")["ok"])

    def test_a_dropped_check_passes_while_the_linters_dont_require_it(self):
        from server import taxo_reader
        self.assertTrue(self.read_back(self.BASE)["ok"])
        with mock.patch.object(taxo_reader, "REQUIRE_CHECK", True):
            self.assertFalse(self.read_back(self.BASE)["ok"])

    def test_misread_check_digits_fail(self):
        self.assertFalse(self.read_back(self.BASE + " check 52753")["ok"])

    def test_locked_pass_hides_the_check(self):
        from server import staleness
        out = staleness.redact({"locked": "outdated", "stage": "prompt", "code": "EVP-1", "run_id": "r1",
                                "fingerprint": "x", "fingerprint_check": "52758"})
        self.assertNotIn("fingerprint_check", out)


if __name__ == "__main__":
    unittest.main()
