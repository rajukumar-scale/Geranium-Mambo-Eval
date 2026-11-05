"""Locked passes (2026-11-05): a first pass through the stages gets every pass right away; once any stage's newest
pass is outdated, every pass loses its codes for the contributor until the outdated stages are sent again.
Task 6adfbc55...6c6f / attempt 6aeb9f8e: rubric and OE passed on Nov 3's SQL, the SQL changed on Nov 4, the old
screenshots were submitted."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, proofs, staleness


class PassLockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.saved = {"universe": "u", "task-details": "td", "prompt": "p1", "injection": "sql1", "oe": "oe1",
                      "rubric": "r1"}
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(staleness, "snapshot", lambda a: dict(self.saved)),
                   mock.patch.object(portal.blocks, "disabled", lambda *a: None),
                   mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None),
                   mock.patch.object(portal.assignment, "schedule", lambda *a: None),
                   mock.patch.object(portal.plan, "maybe_kickoff", lambda *a: None),
                   mock.patch.object(portal.spend, "maybe_notice", lambda *a: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", "t1", "u1", "a1@x.com", now, now))
        (self.tmp / "attempts" / "a1" / "logs").mkdir(parents=True)
        self.n = 0

    def passed(self, stage, verdict="PASS"):
        """A finished review of `stage` that passed on the inputs saved now, with its chat card."""
        self.n += 1
        rid = f"r{self.n}"
        run = {"id": rid, "attempt_id": "a1", "command": stage, "models": {}, "started_at": time.time() - 60,
               "finished_at": time.time(), "cost_estimate": 1.0}
        report = {"stage": stage, "verdict": verdict, "score": 5, "exec_status": "COMPLETE", "file": f"{stage}.md",
                  "sha256": "ab" * 32, "fingerprint": f"{stage} fp {self.n}"}
        chat = db.get_chat("a1")
        payload = proofs.create(run, chat, report, inputs=staleness.snapshot("a1"))
        db.add_message("a1", "proof", f"{stage} {verdict}", payload)
        return payload

    def client(self, admin=False):
        session = {"attempt_id": "a1", "user_id": "u1", "iat": time.time()}
        if admin:
            session["admin"] = "evan@scale.com"
        c = TestClient(portal.app)
        c.cookies.set(auth.USER_COOKIE, auth.sign(session))
        return c

    def me(self, admin=False):
        with mock.patch.object(portal, "admin_role", lambda email: "admin" if admin else None):
            res = self.client(admin).get("/api/me")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_first_pass_through_the_stages_is_never_locked(self):
        for stage in ("injection", "prompt", "hardness", "oe", "rubric"):
            self.passed(stage)
            me = self.me()
            self.assertFalse(me["pass_gate"]["locked"], stage)
            self.assertTrue(all(p["code"] and p["fingerprint"] for p in me["proofs"]))
        self.assertEqual([s["status"] for s in me["pass_gate"]["stages"]], ["current"] * 5)

    def test_a_stage_not_passed_yet_never_locks(self):
        self.passed("injection")
        self.saved["prompt"] = "p2"  # prompt edited before /prompt ever passed: nothing outdated
        gate = self.me()["pass_gate"]
        self.assertFalse(gate["locked"])
        self.assertEqual({s["stage"]: s["status"] for s in gate["stages"]}["prompt"], "none")

    def newest(self, me=None):
        me = me or self.me()
        return {p["stage"]: p for p in me["proofs"]}

    def test_ebad_sequence_unlocks_in_order_and_warns_about_later_stages(self):
        """Everything passed on sql1, the SQL changed, then the stages are sent again in order."""
        for stage in ("injection", "prompt", "hardness", "oe", "rubric"):
            self.passed(stage)
        self.saved["injection"] = "sql2"
        cards = self.newest()
        self.assertEqual(cards["injection"]["locked"], {"self": True, "todo": ["injection"]})
        self.assertEqual(cards["rubric"]["locked"]["todo"], ["injection", "prompt", "hardness", "oe", "rubric"])
        self.assertTrue(all(c["code"] is None for c in cards.values()))
        for stage in ("injection", "prompt", "hardness"):
            self.passed(stage)
        me = self.me()
        self.assertEqual(me["pass_gate"]["todo"], ["oe", "rubric"])
        cards = self.newest(me)
        for stage in ("injection", "prompt", "hardness"):  # usable again, with the warning
            self.assertFalse(cards[stage]["locked"], stage)
            self.assertTrue(cards[stage]["code"] and cards[stage]["fingerprint"], stage)
            self.assertEqual(cards[stage]["warn_later"], ["oe", "rubric"], stage)
        self.assertTrue(cards["oe"]["locked"]["self"] and cards["rubric"]["locked"]["self"])
        self.assertIsNone(cards["rubric"]["code"])
        with mock.patch.object(portal, "admin_role", lambda email: None):
            msgs = self.client().get("/api/messages").json()
        history = {m["meta"]["ref"]: m["meta"] for m in msgs if m["role"] == "proof"}
        self.assertIsNone(history[cards["rubric"]["ref"]]["code"])  # the old rubric card is locked in the chat too
        self.assertTrue(history[cards["hardness"]["ref"]]["code"])
        self.passed("oe")
        cards = self.newest()
        self.assertEqual((cards["oe"]["warn_later"], bool(cards["rubric"]["locked"])), (["rubric"], True))
        self.passed("rubric")
        me = self.me()
        self.assertEqual(me["pass_gate"]["todo"], [])
        self.assertTrue(all(c["code"] and not c["locked"] and not c["warn_later"] for c in self.newest(me).values()))

    def test_a_prompt_change_still_gives_the_new_prompt_card(self):
        """The prompt card is needed for the Opus run behind /hardness: it must never wait on later stages."""
        for stage in ("injection", "prompt", "hardness", "oe", "rubric"):
            self.passed(stage)
        self.saved["prompt"] = "p2"
        cards = self.newest()
        self.assertFalse(cards["injection"]["locked"])  # earlier than the change: still usable
        self.assertEqual(cards["injection"]["warn_later"], ["prompt", "hardness", "oe", "rubric"])
        self.passed("prompt")
        cards = self.newest()
        self.assertFalse(cards["prompt"]["locked"])
        self.assertTrue(cards["prompt"]["code"])
        self.assertEqual(cards["prompt"]["warn_later"], ["hardness", "oe", "rubric"])
        self.assertTrue(cards["hardness"]["locked"]["self"])

    def test_admins_still_see_the_codes(self):
        self.passed("injection")
        self.passed("rubric")
        self.saved["injection"] = "sql2"
        me = self.me(admin=True)
        self.assertTrue(me["pass_gate"]["locked"])
        self.assertTrue(all(p["code"] for p in me["proofs"]))

    def test_pass_tools_refuse_a_locked_pass_for_its_owner(self):
        inj = self.passed("injection")
        prm = self.passed("prompt")
        self.saved["prompt"] = "p2"
        with mock.patch.object(portal, "admin_role", lambda email: None), \
                mock.patch.object(portal.fingerprint, "for_attempt", lambda a, s: None):
            locked = self.client().get(f"/api/proofs/{prm['code']}/input")
            usable = self.client().get(f"/api/proofs/{inj['code']}/input")
        self.assertEqual(locked.status_code, 423)
        self.assertIn("locked", locked.json()["detail"])
        self.assertNotEqual(usable.status_code, 423)  # the earlier injection card isn't locked by a prompt change

    def test_public_verify_page_shows_a_locked_pass_without_its_codes(self):
        p = self.passed("rubric")
        self.saved["injection"] = "sql2"
        with mock.patch.object(portal, "viewer_is_admin", lambda request: False):
            body = TestClient(portal.app).get(f"/api/verify/{p['code']}").json()
        self.assertIsNone(body["fingerprint"])
        self.assertTrue(body["locked"]["self"])

    def test_live_pass_event_is_locked_like_the_history(self):
        self.passed("injection")
        self.passed("oe")
        self.saved["injection"] = "sql2"
        old_oe = [p for p in self.me()["proofs"] if p["stage"] == "oe"][-1]
        event = staleness.view_event("a1", {"event": "proof", "attempt_id": "a1",
                                            "data": {**old_oe, "code": "EVP-X", "fingerprint": "fp", "locked": None}})
        self.assertIsNone(event["data"]["code"])
        self.assertTrue(event["data"]["locked"]["self"])
        fresh = self.passed("injection")
        event = staleness.view_event("a1", {"event": "proof", "attempt_id": "a1", "data": fresh})
        self.assertTrue(event["data"]["code"])  # a fresh injection card is usable, with the warning
        self.assertEqual(event["data"]["warn_later"], ["oe"])
        other = {"event": "message", "attempt_id": "a1", "data": {"role": "assistant", "content": "hi"}}
        self.assertIs(staleness.view_event("a1", other), other)

    def test_notice_warns_about_the_penalty(self):
        self.passed("rubric")
        self.saved["injection"] = "sql2"
        note = staleness.notice("a1")
        self.assertIn("`/rubric`", note)
        self.assertIn("penalized", note)


if __name__ == "__main__":
    unittest.main()
