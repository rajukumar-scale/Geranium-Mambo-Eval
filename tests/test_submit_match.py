"""Submit match (2026-11-11): a submitted prompt/SQL/OE/rubric that isn't the version the chat's passes were reviewed
on alerts Evan once and tells the contributor in their chat (…ec32: 294-char prompt submitted, passes on 238)."""

import asyncio
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, fingerprint, submissions, submit_match

TASK = "6af2896b8da5bdd69784ec32"
OLD = "I'm about to start building levels 301 to 320, can you check every one of them for anything that's broken"
NEW = "I'm about to start building levels 301 to 320, make the Linear tickets Robert asked for"
RUBRIC = [{"id": "r1", "title": "Creates ZOM-1", "weight": 5}]


def b64(text):
    return base64.b64encode(text.encode()).decode()


class SubmitMatchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.saved = {"prompt": NEW, "injection": "insert into x values (1);", "oe": "1. reads", "rubric": json.dumps(RUBRIC)}
        self.sent, self.published = [], []
        self.at = time.time() - 1800
        for p in [mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.object(submissions, "fresh", lambda *a: True),
                  mock.patch.object(submissions, "submitted_at", lambda t, u: self.at if (t, u) == (TASK, "u1") else None),
                  mock.patch.object(fingerprint, "for_attempt", lambda a, s: fingerprint.of_input(s, self.saved[s])),
                  mock.patch.object(submit_match.hub, "publish", lambda *a: self.published.append(a))]:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", TASK, "u1", "cb@x.com", now - 86400, now))
        for stage in ("prompt", "injection", "oe", "rubric"):
            db.ex("insert into proofs(code, run_id, attempt_id, payload, created_at) values (?,?,?,?,?)",
                  (f"C{stage}", f"r{stage}", "a1", json.dumps({"stage": stage}), now - 3600))
        db.ex("insert or replace into status_notified(key, created_at) values (?,?)", (submit_match.BASELINE, now - 86400))

    def row(self, prompt=OLD, landed=True, **over):
        r = {"TASK_ID": TASK, "USER_ID": "u1", "ATTEMPT_ID": "p1", "SUBMITTED_AT": self.at, "LANDED": landed,
             "PROMPT_B64": b64(prompt), "INJECTION_B64": b64(self.saved["injection"]), "DID_INJECT": "yes",
             "OE_B64": b64(self.saved["oe"]), "RUBRIC_B64": b64(json.dumps(RUBRIC))}
        r.update(over)
        return r

    def run_check(self, rows):
        async def send(text):
            self.sent.append(text)
            return True
        with mock.patch.object(submit_match.auth, "redash_rows", mock.AsyncMock(return_value=rows)):
            return asyncio.run(submit_match.check_once(send))

    def test_the_ec32_case_alerts_and_tells_the_contributor_once(self):
        hits = self.run_check([self.row()])
        self.assertEqual([m["stage"] for m in hits[0]["mismatches"]], ["prompt"])  # SQL, OE and rubric match
        self.assertIn("don't match", self.sent[0].lower())
        self.assertIn("prompt: submitted", self.sent[0])
        note = [m for m in db.messages("a1") if m["meta"].get("submit_match")]
        self.assertEqual(len(note), 1)
        self.assertIn("The prompt you submitted is not the version your portal passes were reviewed on", note[0]["content"])
        self.assertEqual(self.run_check([self.row()]), [])  # the same submit isn't checked twice
        self.assertEqual(len(self.sent), 1)

    def test_a_matching_submit_is_quiet(self):
        self.assertEqual(self.run_check([self.row(prompt=NEW)]), [])
        self.assertEqual(self.sent, [])

    def test_not_landed_yet_is_retried(self):
        self.assertEqual(self.run_check([self.row(landed=False, PROMPT_B64=None)]), [])
        self.assertEqual(len(self.run_check([self.row()])), 1)  # landed on the next refresh

    def test_a_missing_injection_counts_when_the_sql_passed(self):
        hits = self.run_check([self.row(prompt=NEW, INJECTION_B64=None, DID_INJECT="no")])
        self.assertEqual([m["stage"] for m in hits[0]["mismatches"]], ["injection"])
        self.assertIn("(empty", hits[0]["mismatches"][0]["submitted"])

    def test_quote_escaping_in_the_rubric_isnt_a_mismatch(self):
        escaped = [{"id": "r1", "title": "Creates \\\"ZOM-1\\\"", "weight": 5}]
        self.saved["rubric"] = json.dumps([{"id": "r1", "title": 'Creates "ZOM-1"', "weight": 5}])
        self.assertEqual(self.run_check([self.row(prompt=NEW, RUBRIC_B64=b64(json.dumps(escaped)))]), [])

    def test_stages_without_a_pass_arent_compared(self):
        db.ex("delete from proofs where attempt_id='a1' and code='Coe'")
        self.assertEqual(self.run_check([self.row(prompt=NEW, OE_B64=b64("something else"))]), [])

    def test_too_old_submits_are_given_up(self):
        self.at = time.time() - submit_match.GIVE_UP - 60
        self.assertEqual(submit_match.due(time.time() - 10 * 86400), [])



class PassScreenPromptTest(unittest.TestCase):
    def test_oe_and_rubric_passes_carry_and_show_their_prompt(self):
        root = Path(__file__).resolve().parent.parent
        src = (root / "server" / "proofs.py").read_text()
        self.assertIn('if report["stage"] in ("oe", "rubric", "hardness"):', src)
        self.assertIn('payload["prompt_fingerprint"] = fingerprint.for_attempt(run["attempt_id"], "prompt")', src)
        js = (root / "web" / "common.js").read_text()
        self.assertIn("This pass is only for this prompt:", js)
        self.assertIn("I've updated my Outlier task, and its prompt is this exact version", js)


if __name__ == "__main__":
    unittest.main()
