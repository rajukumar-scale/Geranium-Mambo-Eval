"""Phase 6 unit tests: LH Redash query bindings, submit_match with REFNOTES_B64, and similarity query ID."""

import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, fingerprint, submissions, submit_match


class LHRedashConfigTests(unittest.TestCase):
    def test_lh_redash_defaults(self):
        self.assertEqual(config.REDASH_LOGIN_QUERY_ID, 387117)
        self.assertEqual(config.BUDGET_QUERY_ID, 397698)
        self.assertEqual(config.BUDGET_DAILY_QUERY_ID, 397699)
        self.assertEqual(config.COST_PORTAL_SMIG_QUERY_ID, 397700)
        self.assertEqual(config.COST_FACTS_QUERY_ID, 397701)
        self.assertEqual(config.COST_TASK_STATES_QUERY_ID, 405007)
        self.assertEqual(config.CB_FACTS_QUERY_ID, 405006)
        self.assertEqual(config.SUBMISSIONS_QUERY_ID, 388752)
        self.assertEqual(config.CLAIMS_QUERY_ID, 388856)
        self.assertEqual(config.SUBMIT_MATCH_QUERY_ID, 404918)
        self.assertEqual(config.BUDGET_KEY_ALIAS, "pub-agentic-if-task")
        self.assertEqual(config.ASSIGNMENT_AUTOFILL, 0)

    def test_bundle_similarity_query_id(self):
        tools = Path(__file__).resolve().parents[2] / "New-Evals-Bundle/Evals/tools"
        if not tools.is_dir():
            self.skipTest("New-Evals-Bundle not present beside portal")
        sys.path.insert(0, str(tools))
        import hg_similarity
        self.assertEqual(hg_similarity.QUERY_ID, 404917)


class SubmitMatchRefNotesTests(unittest.TestCase):
    def test_refnotes_mismatch_detected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = {"prompt": "P1", "injection": "", "reference-notes": "Compliant run: X", "rubric": json.dumps([{"title": "T1", "weight": 5}])}
        with mock.patch.object(config, "DATA_DIR", Path(tmp.name)), \
                mock.patch.object(db, "_conn", None), \
                mock.patch.object(fingerprint, "for_attempt", lambda a, s: fingerprint.of_input(s, saved[s])):
            self.addCleanup(lambda: db._local.__dict__.clear())
            now = time.time()
            db.ex("insert into proofs(code, run_id, attempt_id, payload, created_at) values (?,?,?,?,?)",
                  ("C1", "r1", "a1", json.dumps({"stage": "reference-notes"}), now))
            row = {
                "PROMPT_B64": base64.b64encode(b"P1").decode(),
                "INJECTION_B64": None,
                "RUBRIC_B64": base64.b64encode(saved["rubric"].encode()).decode(),
                "REFNOTES_B64": base64.b64encode(b"Compliant run: OLD").decode(),
            }
            hits = submit_match.mismatches("a1", submit_match.submitted_fingerprints(row))
            self.assertEqual([h["stage"] for h in hits], ["reference-notes"])


if __name__ == "__main__":
    unittest.main()
