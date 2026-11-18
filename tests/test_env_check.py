"""Env data check after /hardness (2026-11-06): records the uploaded Opus run read that neither the base universe nor
the saved SQL has (6aecc5e4: rows left by older SQL drafts; 6aec82c2: data added in the env explorer)."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, env_check


def tool(name, uid, inp=None):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": uid, "name": name, "input": inp or {}}]}}


def result(uid, payload):
    text = json.dumps({"result": json.dumps(payload)})  # the hg tools double-encode their result
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": uid, "content": [{"type": "text", "text": text}]}]}}


RUN = [
    {"type": "system", "subtype": "init"},
    tool("mcp__hg__gdrive_list_folder", "t1"),
    result("t1", {"files": [
        {"id": "d_agr_mkt05_speedrunsofia", "name": "Creator Agreement - SpeedrunSofia",
         "permissions": [{"id": "f6bbf02ced77", "role": "reader"}]},
        {"id": "d_base_doc_0001", "name": "Base doc"}]}),
    tool("mcp__hg__gmail_search_messages", "t2"),
    result("t2", {"messages": [{"id": "frederick.stone@harmonygames.co/<wise-1018.20251001@wise.com>",
                                "threadId": "frederick.stone@harmonygames.co/th_base_77", "subject": "Wise transfer"}]}),
    tool("mcp__hg__linear_list_issues", "t3"),
    result("t3", {"issues": [{"id": "ZOM-444", "title": "Base ticket"}, {"id": "ZOM-601", "title": "Injected ticket"}]}),
    tool("mcp__hg__slack_send_message", "t4"),
    result("t4", {"ok": True, "ts": "1799999999.000100"}),
    tool("mcp__hg__slack_search_public_and_private", "t5"),
    result("t5", {"messages": [{"ts": "1799999999.000100", "text": "the agent's own post"}]}),
    tool("Bash", "t6"),
    result("t6", {"files": [{"id": "d_not_from_the_env"}]}),
]


class ReadIdsTest(unittest.TestCase):
    def test_keeps_data_ids_and_drops_runtime_and_agent_ids(self):
        ids = env_check.read_ids(json.dumps(RUN))
        self.assertEqual(set(ids), {"d_agr_mkt05_speedrunsofia", "d_base_doc_0001",
                                    "frederick.stone@harmonygames.co/<wise-1018.20251001@wise.com>",
                                    "frederick.stone@harmonygames.co/th_base_77", "ZOM-444", "ZOM-601"})
        self.assertEqual(ids["d_agr_mkt05_speedrunsofia"][1], "Creator Agreement - SpeedrunSofia")

    def test_jsonl_export_and_unknown_formats(self):
        self.assertEqual(env_check.read_ids("\n".join(json.dumps(e) for e in RUN)), env_check.read_ids(json.dumps(RUN)))
        self.assertIsNone(env_check.read_ids("not json {"))
        self.assertIsNone(env_check.read_ids('"a string"'))


class UnknownTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.universe = Path(tmp.name)
        (self.universe / "svc").mkdir()
        (self.universe / "svc" / "data.json").write_text(json.dumps(
            {"files": [{"id": "d_base_doc_0001"}], "threads": ["<th_base_77>"], "issues": [{"identifier": "ZOM-444"}]}))

    def unknown(self, sql=""):
        for rg in (False, True):  # ripgrep on the server; the plain scan when it isn't installed
            with self.subTest(rg=rg):
                if not rg:
                    with mock.patch.object(env_check.shutil, "which", lambda name: None):
                        out = env_check.unknown_records(json.dumps(RUN), sql, self.universe)
                elif env_check.shutil.which("rg"):
                    self.assertEqual(env_check.unknown_records(json.dumps(RUN), sql, self.universe), out)
        return out

    def test_records_in_neither_base_nor_sql(self):
        self.assertEqual(sorted(s for s, _, _ in self.unknown()),
                         ["ZOM-601", "d_agr_mkt05_speedrunsofia", "wise-1018.20251001@wise.com"])

    def test_the_sql_explains_them(self):
        sql = "INSERT INTO linear.issues VALUES ('ZOM-601'); -- d_agr_mkt05_speedrunsofia <wise-1018.20251001@wise.com>"
        self.assertEqual(self.unknown(sql), [])

    def test_a_longer_id_is_not_hidden_by_a_shorter_one(self):
        (self.universe / "svc" / "more.json").write_text('"ZOM-60"')  # ZOM-60 in base must not count for ZOM-601
        self.assertIn("ZOM-601", [s for s, _, _ in self.unknown()])

    def test_card_names_the_records_and_the_fix(self):
        text = env_check.card_text(self.unknown())
        self.assertIn("read **3** records", text)
        self.assertIn("- Drive: `d_agr_mkt05_speedrunsofia` (Creator Agreement - SpeedrunSofia)", text)
        self.assertIn("redeploy the env, apply only your final SQL", text)


class CheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        base = self.tmp / "base"
        (base / "Universe_SynthCut").mkdir(parents=True)
        (base / "Universe_SynthCut" / "data.json").write_text('"d_base_doc_0001" "th_base_77" "ZOM-444"')
        self.inputs = self.tmp / "inputs"
        (self.inputs / "opus").mkdir(parents=True)
        (self.inputs / "opus" / "trajectory.json").write_text(json.dumps(RUN))
        (self.inputs / "01_Universe_Inject.sql").write_text("-- ZOM-601")
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(config, "EVAL_BASE_DIR", base),
                   mock.patch.object(env_check.workspace, "universe_date", lambda a: "synthcut"),
                   mock.patch.object(env_check.workspace, "task_inputs", lambda a: self.inputs),
                   mock.patch.object(env_check.workspace, "saved_inputs", lambda a: [
                       {"key": "injection", "state": "saved", "files": [{"path": "Evals/task_inputs/01_Universe_Inject.sql"}]}]),
                   mock.patch.object(env_check.hub, "publish", lambda *a: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
              "values (?,?,?,?,?,?,?)", ("a1", "t1", "u1", "cb@outlier.ai", "s1", now, now))
        self.chat = db.get_chat("a1")

    def run_check(self, admin=0, command="hardness", status="done"):
        run = {"id": "r1", "kind": "review", "command": command, "actor_admin": admin}
        return asyncio.run(env_check.after_review(self.chat, run, status))

    def test_posts_the_card_and_holds_it_until_acknowledged(self):
        msg = self.run_check()
        self.assertEqual(msg["meta"]["env_check"], 2)  # SpeedrunSofia + the Wise email; ZOM-601 is in the SQL
        self.assertIn("SpeedrunSofia", msg["content"])
        self.assertEqual(env_check.pending("a1"), {"id": msg["id"], "content": msg["content"]})
        self.assertFalse(env_check.acknowledge("a1", msg["id"] + 99))
        self.assertTrue(env_check.acknowledge("a1", msg["id"]))
        self.assertIsNone(env_check.pending("a1"))
        again = self.run_check()  # a new run that still reads them: a new card, pending again
        self.assertEqual(env_check.pending("a1")["id"], again["id"])

    def test_no_clean_note(self):
        """A run that didn't read the old rows doesn't prove they're gone (6aecc5e4's final upload was clean)."""
        self.run_check()
        clean = [e for e in RUN if "speedrunsofia" not in json.dumps(e).lower() and "wise-1018" not in json.dumps(e)]
        (self.inputs / "opus" / "trajectory.json").write_text(json.dumps(clean))
        self.assertIsNone(self.run_check())
        self.assertIsNotNone(env_check.pending("a1"))  # the earlier card still needs its Acknowledge

    def test_endpoint_and_me(self):
        from fastapi.testclient import TestClient
        from server import app as portal, auth
        msg = self.run_check()

        def client(admin=False):
            c = TestClient(portal.app)
            c.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "a1", "user_id": "u1", "iat": time.time(),
                                                       **({"admin": "evan@scale.com"} if admin else {})}))
            return c
        with mock.patch.object(portal.blocks, "disabled", lambda *a: None), \
                mock.patch.object(portal, "admin_role", lambda email: "admin"):
            self.assertEqual(client(admin=True).post("/api/env-check/ack", json={"message_id": msg["id"]}).status_code, 403)
            self.assertIsNone(portal._env_alert({"attempt_id": "a1", "admin": "evan@scale.com"}))
            self.assertEqual(portal._env_alert({"attempt_id": "a1"})["id"], msg["id"])
            self.assertEqual(client().post("/api/env-check/ack", json={"message_id": msg["id"]}).status_code, 200)
            self.assertIsNone(portal._env_alert({"attempt_id": "a1"}))

    def test_the_page_blocks_until_acknowledged(self):
        web = Path(__file__).resolve().parent.parent / "web"
        app_js, common = (web / "app.js").read_text(), (web / "common.js").read_text()
        self.assertIn("showEnvAlert();", app_js)
        self.assertIn('"/api/env-check/ack"', app_js)
        self.assertIn('p.stage === "hardness" && typeof showEnvAlert === "function"', common)
        self.assertIn('const INJECT_REMINDER_STAGES = new Set(["prompt", "rubric", "stumping"])', common)
        self.assertIn("answer <b>Yes</b> to “did you inject”", common)

    def test_only_contributors_finished_hardness(self):
        self.assertIsNone(self.run_check(admin=1))
        self.assertIsNone(self.run_check(command="prompt"))
        self.assertIsNone(self.run_check(status="failed"))
        with mock.patch.object(config, "ENV_CHECK_PILOT", []):
            self.assertIsNone(self.run_check())

    def test_the_runner_schedules_it(self):
        src = (Path(__file__).resolve().parent.parent / "server" / "runner.py").read_text()
        self.assertIn("env_check.schedule(chat, db.get_run(run_id), status)", src)


if __name__ == "__main__":
    unittest.main()
