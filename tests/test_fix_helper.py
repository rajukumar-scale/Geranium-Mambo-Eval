"""The /prompt fix helper (pilot FIX_HELPER_PILOT, Evan 2026-11-01): the 2nd and later /prompt FAILs get a fix-help offer,
the first plain message after it carries the fix rules + their answer, a /prompt pass gets the "start your Opus run"
step. Never for admins, archived/blocked/load-test chats, or with the flag off."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, fix_helper, runner, staleness, workspace

ANSWER = ("The agent should list every Q3 contract that still needs legal sign-off and DM Owen the list. "
          "The careless agent misses that Arthur withdrew his approval of the Brightline contract in Slack.")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(config, "FIX_HELPER_PILOT", ["cb@outlier.ai"]),
                   mock.patch.object(workspace, "attempt_dir", lambda a: self.tmp / "attempts" / a),
                   mock.patch.object(staleness, "snapshot", lambda a: {}),
                   mock.patch.object(runner.Runner, "_check_spend", mock.AsyncMock()),
                   mock.patch.object(runner.Runner, "_stale_notice", lambda *a: None),
                   mock.patch.object(runner, "run_cost", lambda run, cc: 1.0),
                   mock.patch.object(runner.fingerprint, "for_attempt", lambda a, s: "fp"),
                   mock.patch.object(runner.rerun_guard, "record", lambda *a: None),
                   mock.patch.object(portal.blocks, "disabled", lambda *a: None),
                   mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None),
                   mock.patch.object(fix_helper.blocks, "chat_blocked", lambda *a: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        for attempt, email in (("a1", "cb@outlier.ai+outlier"), ("a2", "other@outlier.ai")):
            db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
                  "values (?,?,?,?,?,?,?)", (attempt, "t" + attempt, "u" + attempt, email, "s" + attempt, now, now))
            (self.tmp / "attempts" / attempt / "logs").mkdir(parents=True)
        self.n = 0

    # ---- a /prompt review finishing, the way runner stores it -------------------------------------------------
    def review(self, verdict, attempt="a1", command="prompt", actor_admin=0):
        self.n += 1
        rid = f"r{self.n}"
        db.ex("insert into runs(id, attempt_id, kind, command, status, session_id, created_at, started_at, actor_admin) "
              "values (?,?,?,?,?,?,?,?,?)", (rid, attempt, "review", command, "running", "S", time.time(), time.time(),
                                             actor_admin))
        report = f"# /{command} report\nVerdict: {verdict}\n"
        parsed = {"stage": command, "sha256": "x", "verdict": verdict, "score": 2 if verdict == "FAIL" else 5,
                  "exec_status": "COMPLETE", "hashes": {}, "fields": {}}
        outcome = {"result": {"result": f"/{command}: {verdict}", "is_error": False, "subtype": "success"},
                   "texts": [], "reports": {config.REPORT_FILES[command]: report}}
        with mock.patch.object(runner.workspace, "parse_report", lambda stage, text: dict(parsed)), \
                mock.patch.object(runner.proofs, "create", lambda *a, **k: {"stage": command, "verdict": verdict}):
            asyncio.run(runner.Runner()._complete(rid, outcome))
        time.sleep(0.002)
        return rid

    def system(self, attempt="a1", key="fix_help_offer"):
        return [m for m in db.messages(attempt) if m["role"] == "system" and m["meta"].get(key)]

    # ---- the contributor sending a message ------------------------------------------------------------------
    def send(self, text, attempt="a1", admin=None):
        submitted = []
        session = {"attempt_id": attempt, "user_id": "u" + attempt, "iat": time.time(), **({"admin": admin} if admin else {})}
        with mock.patch.object(portal.RUNNER, "submit", submitted.append), \
                mock.patch.object(portal.rerun_guard, "same_as_last", lambda *a: None), \
                mock.patch.object(portal, "admin_role", lambda email: "admin" if admin else None):
            client = TestClient(portal.app)
            client.cookies.set(auth.USER_COOKIE, auth.sign(session))
            res = client.post("/api/send", json={"text": text})
        self.assertEqual(res.status_code, 200, res.text)
        run = db.get_run(submitted[0])
        db.update("runs", "id", run["id"], status="done")  # finished, so the next message can start
        time.sleep(0.002)
        return (self.tmp / "attempts" / attempt / "logs" / f"{run['id']}.prompt.txt").read_text()

    def last_user_meta(self, attempt="a1"):
        return [m for m in db.messages(attempt) if m["role"] == "user"][-1]["meta"]


class OfferTest(Base):
    def test_no_offer_on_the_first_fail(self):
        self.review("FAIL")
        self.assertEqual(self.system(), [])
        self.assertTrue(any(m["role"] == "report" for m in db.messages("a1")))  # the report itself, as today

    def test_offer_on_the_second_and_third_fail(self):
        self.review("FAIL")
        second = self.review("FAIL")
        third = self.review("FAIL")
        offers = self.system()
        self.assertEqual([o["meta"]["run_id"] for o in offers], [second, third])
        self.assertEqual(offers[0]["content"], fix_helper.OFFER)
        self.assertTrue(offers[0]["content"].startswith("This one didn't pass yet. Want help fixing it? Tell me"))
        # posted under the report of the run that made it
        msgs = db.messages("a1")
        report_i = max(i for i, m in enumerate(msgs) if m["role"] == "report" and m["meta"]["run_id"] == second)
        self.assertLess(report_i, msgs.index(offers[0]))

    def test_fails_are_counted_per_attempt_and_per_command(self):
        self.review("FAIL", command="injection")
        self.review("FAIL", attempt="a2")
        self.review("FAIL")
        self.assertEqual(self.system(), [])
        self.review("NON-FAIL")  # a pass in between doesn't reset the count
        self.review("FAIL")
        self.assertEqual(len(self.system()), 1)

    def test_offer_again_reminds_them_of_their_answer(self):
        self.review("FAIL")
        self.review("FAIL")
        self.send(ANSWER + " " + "x" * 300)
        self.review("FAIL")
        again = self.system()[-1]["content"]
        self.assertIn("Last time you told me:", again)
        self.assertIn("same idea", again)
        self.assertIn("The agent should list every Q3 contract", again)
        reminder = again.split('"')[1]
        self.assertLessEqual(len(reminder), fix_helper.REMINDER_CHARS + 5)
        self.assertTrue(reminder.endswith("…"))

    def test_no_message_after_a_pass(self):
        self.review("FAIL")
        for verdict in ("PASS", "NON-FAIL"):
            self.review(verdict)
        self.assertEqual(self.system() + self.system(key="next_step"), [])

    def test_once_per_review(self):
        self.review("FAIL")
        rid = self.review("FAIL")
        chat, run = db.get_chat("a1"), db.get_run(rid)
        self.assertIsNone(fix_helper.after_review(chat, run, "done", "FAIL"))
        self.assertEqual(len(self.system()), 1)

    def test_flag_off_or_not_in_pilot(self):
        with mock.patch.object(config, "FIX_HELPER_PILOT", []):
            self.review("FAIL")
            self.review("FAIL")
            self.review("PASS")
        self.review("FAIL", attempt="a2")
        self.review("FAIL", attempt="a2")
        self.assertEqual(self.system() + self.system(key="next_step") + self.system("a2"), [])
        with mock.patch.object(config, "FIX_HELPER_PILOT", ["*"]):
            self.review("FAIL", attempt="a2")
        self.assertEqual(len(self.system("a2")), 1)

    def test_pilot_matching(self):
        chat = db.get_chat("a1")
        for value, expected in ((["CB@outlier.ai"], True), (["ua1"], True), (["*"], True), ([], False),
                                (["atlasadmin"], False)):
            with self.subTest(value=value), mock.patch.object(config, "FIX_HELPER_PILOT", value):
                self.assertEqual(fix_helper.in_pilot(chat), expected)

    def test_not_for_admin_runs_archived_blocked_or_loadtest_chats(self):
        self.review("FAIL")
        self.review("FAIL", actor_admin=1)
        self.review("PASS", actor_admin=1)
        self.assertEqual(self.system() + self.system(key="next_step"), [])
        db.update("chats", "attempt_id", "a1", archived_from="x")
        self.review("FAIL")
        db.update("chats", "attempt_id", "a1", archived_from=None, task_id="loadtest")
        self.review("FAIL")
        db.update("chats", "attempt_id", "a1", task_id="t1")
        with mock.patch.object(fix_helper.blocks, "chat_blocked", lambda a: {"reason": ""}):
            self.review("FAIL")
        self.assertEqual(self.system(), [])
        self.review("FAIL")
        self.assertEqual(len(self.system()), 1)

    def test_a_helper_error_never_breaks_finishing_the_run(self):
        self.review("FAIL")
        with mock.patch.object(fix_helper, "after_review", side_effect=RuntimeError("boom")), \
                mock.patch.object(runner.Runner, "_stale_notice") as stale, \
                self.assertLogs("portal.runner", "ERROR"):
            rid = self.review("FAIL")
        self.assertEqual(db.get_run(rid)["status"], "done")
        self.assertEqual(db.get_run(rid)["verdict"], "FAIL")
        stale.assert_called_once()  # the rest of _complete still ran
        self.assertEqual(self.system(), [])


class NoteTest(Base):
    def offered(self):
        self.review("FAIL")
        self.review("FAIL")
        self.assertEqual(len(self.system()), 1)

    def test_note_on_the_first_plain_message_only(self):
        self.offered()
        prompt = self.send(ANSWER)
        self.assertIn(fix_helper.rules_text(), prompt)
        self.assertNotIn("intent.md", prompt)
        self.assertTrue(prompt.rstrip().endswith(ANSWER))
        self.assertEqual(prompt.count(ANSWER), 1)
        meta = self.last_user_meta()
        self.assertEqual((meta["fix_help_offer_id"], meta["fix_help_answer"]), (self.system()[0]["id"], ANSWER))
        self.assertEqual(self.send("and what about the rubric?"), "and what about the rubric?")
        self.assertNotIn("fix_help_answer", self.last_user_meta())

    def test_not_on_commands_and_the_next_plain_message_still_gets_it(self):
        self.offered()
        prompt = self.send("/injection")
        self.assertNotIn("Portal note", prompt)
        self.assertIn("Portal note", self.send(ANSWER))

    def test_not_after_a_newer_prompt_run(self):
        self.offered()
        self.review("NON-FAIL")
        self.assertEqual(self.send(ANSWER), ANSWER)

    def test_not_in_other_chats(self):
        self.offered()
        with mock.patch.object(config, "FIX_HELPER_PILOT", ["*"]):
            self.assertEqual(self.send(ANSWER, attempt="a2"), ANSWER)
        self.assertIn("Portal note", self.send(ANSWER))

    def test_not_when_the_flag_is_off(self):
        self.offered()
        with mock.patch.object(config, "FIX_HELPER_PILOT", []):
            self.assertEqual(self.send(ANSWER), ANSWER)

    def test_not_for_an_admin_sending_in_their_chat(self):
        self.offered()
        self.assertEqual(self.send("checking in", admin="evan@scale.com"), "checking in")
        self.assertIn("Portal note", self.send(ANSWER))  # the admin's message didn't use it up

    def test_not_for_an_answer_to_a_waiting_review(self):
        self.offered()
        db.ex("insert into runs(id, attempt_id, kind, command, status, session_id, created_at) values (?,?,?,?,?,?,?)",
              ("rq", "a1", "review", "injection", "done", "S-q", time.time() - 1))  # older than the offer
        db.update("chats", "attempt_id", "a1", pending_review_run="rq")
        prompt = self.send("Task date: Feb 14")
        self.assertNotIn(fix_helper.rules_text(), prompt)
        self.assertIn("waiting for something from them", prompt)  # the review's own note
        db.update("chats", "attempt_id", "a1", pending_review_run=None)  # the /injection continuation saved its report
        self.assertIn("Portal note, hidden", self.send(ANSWER))  # not used up by the review's answer

    def test_same_idea_uses_the_stored_answer(self):
        self.offered()
        self.send(ANSWER)
        self.review("FAIL")
        for reply in ("same idea", "Same idea.", "same"):
            with self.subTest(reply=reply):
                prompt = self.send(reply)
                self.assertIn(f"<<<\n{ANSWER}\n>>>", prompt)
                self.assertTrue(prompt.rstrip().endswith(reply))
                meta = self.last_user_meta()
                self.assertEqual((meta["fix_help_answer"], meta["fix_help_reused"]), (ANSWER, True))
                self.review("FAIL")  # a fresh offer for the next reply

    def test_same_idea_with_an_update(self):
        self.offered()
        self.send(ANSWER)
        self.review("FAIL")
        prompt = self.send("Same idea, but the withdrawal is now in an email from Arthur to Owen")
        self.assertIn(f"<<<\n{ANSWER}\n>>>", prompt)
        self.assertIn("adds an update", prompt)
        self.assertEqual(self.last_user_meta()["fix_help_answer"],
                         ANSWER + "\n\nUpdate: but the withdrawal is now in an email from Arthur to Owen")

    def test_a_new_answer_replaces_the_old_one(self):
        self.offered()
        self.send(ANSWER)
        self.review("FAIL")
        self.send("New plan: the agent should reconcile the Q3 invoices against the Jira budget tickets.")
        self.review("FAIL")
        self.assertIn("New plan: the agent should", self.system()[-1]["content"])

    def test_a_short_first_reply_asks_the_questions_and_is_not_stored(self):
        self.offered()
        prompt = self.send("yes please")
        self.assertIn("ask them the two questions", prompt)
        self.assertIsNone(self.last_user_meta()["fix_help_answer"])
        self.review("FAIL")
        self.assertEqual(self.system()[-1]["content"], fix_helper.OFFER)  # nothing to remind them of

    def test_the_reminder_is_one_line_and_escaped(self):
        self.assertEqual(fix_helper._reminder("a *b*\n\n[c](d) <e>"), r"a \*b\* \[c\](d) \<e\>")


class TextsTest(unittest.TestCase):
    def test_rules_file_is_v5_with_the_answer_in_the_note(self):
        rules = fix_helper.rules_text()
        self.assertTrue(rules.startswith("The contributor's answer to"))
        self.assertIn("quoted at the end of this note", rules)
        self.assertNotIn("intent.md", rules)
        self.assertNotIn("<!--", rules)
        for need in ("guidelines/known_data_issues.md", "guidelines/hardness_v2.md", "Neighbor check",
                     "rerun /injection if the SQL changed, then /prompt."):
            self.assertIn(need, rules)

    def test_guard_is_untouched(self):
        self.assertNotIn(fix_helper.rules_text()[:80], runner.GUARD)

    def test_offer_text(self):
        self.assertEqual(fix_helper.OFFER,
                         "This one didn't pass yet. Want help fixing it? Tell me in a few sentences:\n"
                         "- what should the agent end up doing?\n"
                         "- what's the one thing it should catch that a careless agent would miss?\n\n"
                         "I'll work only on what the report failed, starting from what you meant.")


if __name__ == "__main__":
    unittest.main()
