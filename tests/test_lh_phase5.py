"""Phase 5 unit tests: LH fingerprints, proofs, taxo_reader tags & prompts, proxy tags/redaction, Taxonomy alignment."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, fingerprint, proofs, proxy, taxo_check, taxo_reader, workspace


class LHFingerprintAndProofTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patches = [
            mock.patch.object(config, "DATA_DIR", self.tmp),
            mock.patch.object(config, "EVAL_BASE_DIR", self.tmp / "eval-base"),
            mock.patch.object(config, "PROOF_SECRET", "test-proof-secret"),
            mock.patch.object(db, "_conn", None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, project_id, user_id, email, created_at, last_activity) "
              "values (?,?,?,?,?,?,?)", ("a1", "t1", "6ae1792488162884415d5ee6", "u1", "cb@outlier.ai", now, now))

    def _save_slot(self, key, filename, text):
        root = workspace.ensure_attempt("a1", unpack=False)
        ti = root / "task_inputs"
        p = ti / filename
        p.write_text(text, encoding="utf-8")
        idx_p = ti / "inputs.json"
        idx = json.loads(idx_p.read_text()) if idx_p.is_file() else {"format_version": 1, "revision": 0, "inputs": {}}
        idx["revision"] += 1
        idx["inputs"][key] = {"state": "saved", "revision": idx["revision"], "files": [
            {"filename": filename, "path": f"Evals/task_inputs/{filename}",
             "bytes": p.stat().st_size, "sha256": "0" * 64}
        ]}
        idx_p.write_text(json.dumps(idx))

    def test_all_six_stages_fingerprint_and_prompt_binding(self):
        self._save_slot("injection", "01_Injection.sql", "INSERT INTO slack.slack_messages VALUES ('1');")
        self._save_slot("prompt", "03_Prompt.md", "Check Bellweather payouts in Stripe and draft email to Priya.")
        self._save_slot("rubric", "05_Rubric.json", json.dumps([{"title": "The Agent drafts email.", "weight": 5}]))
        self._save_slot("stumping-check", "07_Stumping_Check.md", "Task Case: A\nGap: 22.9")
        self._save_slot("all-fail-justify", "11_All-Fail-Rubrics-Justification.md", "R4 fails legitimately.")
        self._save_slot("reference-notes", "12_Reference_Notes.md", "Compliant run: checks Stripe.\nTraps: stale QBR.")

        prompt_fp = fingerprint.for_attempt("a1", "prompt")
        self.assertIn("chars —", prompt_fp)
        for st in ("injection", "prompt", "rubric", "stumping", "all-fail-validation", "reference-notes"):
            fp = fingerprint.for_attempt("a1", st)
            self.assertIsNotNone(fp, st)
            line = taxo_reader.taxonomy_line({"stage": st, "fingerprint": fp})
            self.assertTrue(line, st)
            self.assertIn(st, taxo_reader.PROMPTS)

        # Fallback to EVP code when stumping-check & grading-base are absent
        (workspace.task_inputs("a1") / "inputs.json").write_text(json.dumps({"format_version": 1, "revision": 1, "inputs": {}}))
        self.assertIsNone(fingerprint.for_attempt("a1", "stumping"))

        # proofs.create attaches prompt_fingerprint on all stages downstream of prompt
        self._save_slot("prompt", "03_Prompt.md", "Prompt v1 text")
        for st in ("rubric", "stumping", "all-fail-validation", "reference-notes"):
            run = {"id": f"run-{st}", "attempt_id": "a1", "command": st, "models": {}, "started_at": 1, "finished_at": 2}
            chat = db.get_chat("a1")
            rep = {"stage": st, "verdict": "PASS", "score": 5, "exec_status": "COMPLETE",
                   "file": f"{st}.md", "sha256": "a" * 64, "fingerprint": "fp"}
            payload = proofs.create(run, chat, rep)
            self.assertEqual(payload["prompt_fingerprint"], fingerprint.for_attempt("a1", "prompt"), st)

    def test_taxo_check_supports_refnotes_and_criterion_key(self):
        res = taxo_check.compare("reference-notes", "Compliant run: X", fingerprint.text("reference-notes", "Compliant run: X"))
        self.assertTrue(res["match"])
        rub_fp = fingerprint.rubric([{"criterion": "The Agent reports X.", "weight": 5}])
        res_r = taxo_check.compare("rubric", json.dumps([{"criterion": "The Agent reports X.", "weight": 5}]), rub_fp)
        self.assertTrue(res_r["match"])


class TaxoReaderTagsAndTaxonomySyncTests(unittest.TestCase):
    def test_reader_sends_project_litellm_tags(self):
        seen_headers = {}

        class FakeResp:
            status_code = 200
            content = b"{}"
            def raise_for_status(self): pass
            def json(self):
                return {"choices": [{"message": {"content": '{"verdict":"PASS","score":1,"verification_code":"10 chars - abcd ... wxyz","reason":"ok"}'}}]}

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def post(self, url, json=None, headers=None):
                seen_headers.update(headers or {})
                return FakeResp()

        with mock.patch.object(taxo_reader.httpx, "AsyncClient", FakeClient), \
                mock.patch.object(config, "ALLOWED_PROJECT_IDS", ["6ae1792488162884415d5ee6"]), \
                mock.patch.object(config, "READER_API_KEY", "sk-test"):
            out = asyncio.run(taxo_reader.read("stumping", b"png"))
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(seen_headers.get("x-litellm-tags"), "projectId:6ae1792488162884415d5ee6,useCase:eval-portal,stage:reader-stumping")

    def test_taxonomy_json_matches_reader_prompts(self):
        tx_path = Path(__file__).resolve().parents[2] / "Taxonomy/Taxonomy.json"
        if not tx_path.is_file():
            self.skipTest("Taxonomy.json not present")
        tx = json.loads(tx_path.read_text())
        by_id = {n["id"]: n["params"]["expression"] for n in tx["nodes"] if n.get("type") == "jsonata"}
        for nid, stage in (
            ("transformer-jsonata-eval-injection-prompt", "injection"),
            ("transformer-jsonata-eval-prompt-prompt", "prompt"),
            ("transformer-jsonata-eval-rubric-prompt", "rubric"),
            ("transformer-jsonata-eval-stumping-prompt", "stumping"),
            ("transformer-jsonata-eval-allfail-prompt", "all-fail-validation"),
            ("transformer-jsonata-eval-refnotes-prompt", "reference-notes"),
        ):
            self.assertIn(json.dumps(taxo_reader.PROMPTS[stage], ensure_ascii=False)[1:-1], by_id[nid], nid)
        self.assertIn("Titles 3120 letters", taxo_reader.PROMPTS["rubric"])


class ProxyFallbackAndRedactionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.seen_tags = None
        def upstream(request):
            self.seen_tags = request.headers.get("x-litellm-tags")
            return httpx.Response(401, json={"error": {"message": "bad key sk-secretkey1234567890abcd rejected"}})

        for target, name, value in (
            (config, "DATA_DIR", Path(tmp.name)),
            (config, "ALLOWED_PROJECT_IDS", ["6ae1792488162884415d5ee6"]),
            (config, "LITELLM_API_KEY", "sk-main"),
            (config, "LITELLM_FALLBACK_API_KEY", ""),
            (db, "_conn", None),
            (proxy, "ACTIVE_TOKENS", {}),
            (proxy, "_TAGS", {}),
            (proxy, "_client", httpx.AsyncClient(transport=httpx.MockTransport(upstream))),
        ):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, project_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?,?)",
              ("a1", "t1", "", "u1", "admin@scale.com", now, now))
        db.ex("insert into runs(id, attempt_id, kind, command, status, proxy_token, actor_user_id, created_at) values (?,?,?,?,?,?,?,?)",
              ("r1", "a1", "review", "stumping", "running", "evp-tok", "u1", now))

    def test_project_id_fallback_and_secret_redaction(self):
        client = TestClient(proxy.app)
        res = client.post("/v1/messages", headers={"authorization": "Bearer evp-tok"}, json={"model": "claude-opus-5-5"})
        self.assertEqual(res.status_code, 401)
        self.assertIn("projectId:6ae1792488162884415d5ee6", self.seen_tags)
        self.assertNotIn("sk-secretkey1234567890abcd", res.text)
        self.assertIn("sk-[REDACTED]", res.text)


if __name__ == "__main__":
    unittest.main()
