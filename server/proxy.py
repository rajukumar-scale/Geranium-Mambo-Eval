"""Metering proxy between the eval runner and LiteLLM.

The runner never sees the real LiteLLM key. Each run gets a random token that
is only valid while that run is active; this proxy swaps it for the real key,
forwards the request, and records the token usage of every model call
(including subagent calls, which Claude Code's own summary can miss).

It runs as its own service (eval-portal-meter), so a portal restart never cuts a running review's model calls. A token
is checked against the runs table (runs.proxy_token, kept while the run lives), so the two processes share nothing in
memory; ACTIVE_TOKENS is only a shortcut when both run in one process (local development).
"""

import asyncio
import json
import logging
import random

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import config, db

log = logging.getLogger("portal.proxy")

# token -> run_id, only for runs that are currently executing (same-process shortcut; the runs table is the truth)
ACTIVE_TOKENS = {}
CANCELLED = "cancelled by user"  # runner.CANCELLED: a cancelled run's calls stop at once

HOP_HEADERS = {"host", "content-length", "authorization", "x-api-key", "connection", "accept-encoding", "x-litellm-tags",
               "transfer-encoding", "keep-alive", "proxy-authorization", "te", "trailer", "upgrade"}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection", "keep-alive"}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_client = None


def client():
    global _client
    if _client is None:
        # httpx defaults to 100 connections; 50 reviews with parallel sub-agent calls can need several hundred.
        _client = httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=900, write=120, pool=120),
                                    limits=httpx.Limits(max_connections=1000, max_keepalive_connections=200))
    return _client


def _token(request):
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


_TAGS = {}  # run_id -> x-litellm-tags value


def _tags(run_id):
    """Spend-attribution tags for this run's calls. LiteLLM, and SMIG behind it, keep them on each usage row
    (SMIG reads x-litellm-tags only). The contributor is whoever started the run, as in the portal's own spend."""
    if run_id not in _TAGS:
        run = db.one("select r.task_id, r.actor_user_id, r.kind, r.command, c.project_id from runs r "
                     "left join chats c on c.attempt_id = r.attempt_id where r.id=?", (run_id,)) or {}
        stage = "followup" if run.get("kind") == "followup" else (run.get("command") or run.get("kind") or "")
        tags = {"projectId": run.get("project_id"), "taskId": run.get("task_id"), "contributorId": run.get("actor_user_id"),
                "useCase": "eval-portal", "stage": stage.strip("/"), "runId": run_id}
        if len(_TAGS) > 5000:
            _TAGS.clear()
        _TAGS[run_id] = ",".join(f"{k}:{v}" for k, v in tags.items() if v and "," not in str(v))
    return _TAGS[run_id]


def _cost(headers):
    try:
        return float(headers.get("x-litellm-response-cost") or 0)
    except ValueError:
        return 0.0


class SSEUsage:
    """Pull usage out of an Anthropic messages event stream without altering it."""

    def __init__(self):
        self.buffer = b""
        self.usage = {}

    def feed(self, chunk):
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            if event.get("type") == "message_start":
                self._merge(event.get("message", {}).get("usage") or {})
            elif event.get("type") == "message_delta":
                self._merge(event.get("usage") or {})

    def _merge(self, usage):
        # message_delta carries cumulative counts; keep the largest value seen per field.
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                self.usage[key] = max(self.usage.get(key, 0), int(value))


def run_for(token):
    """The run a token belongs to, while that run is queued or running and not being cancelled; else None."""
    if not token:
        return None
    if token in ACTIVE_TOKENS:
        return ACTIVE_TOKENS[token]
    row = db.one("select id from runs where proxy_token=? and status in ('queued','running') and coalesce(error,'')!=?",
                 (token, CANCELLED))
    return row["id"] if row else None


@app.get("/__meter_health")
def meter_health():
    return {"ok": True}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def forward(path: str, request: Request):
    run_id = run_for(_token(request))
    if not run_id:
        return JSONResponse({"type": "error", "error": {"type": "authentication_error",
                                                        "message": "run token is not active"}}, status_code=401)
    body = await request.body()
    is_message = request.method == "POST" and path.rstrip("/").endswith("v1/messages")
    model = None
    if is_message:
        try:
            model = json.loads(body).get("model")
        except ValueError:
            pass
        run = db.get_run(run_id)
        if run and run.get("loadtest"):  # canned reply after a model-like pause; never reaches LiteLLM
            await asyncio.sleep(random.uniform(0.5, 3.0))
            usage = {"input_tokens": 12, "output_tokens": 4}
            db.record_call(run_id, model or "loadtest", path, 200, usage, 0.0)
            return JSONResponse({"id": "msg_loadtest", "type": "message", "role": "assistant", "model": model or "loadtest",
                                 "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": usage})
        if run and config.MAX_CALLS_PER_RUN and run["calls"] >= config.MAX_CALLS_PER_RUN:
            return JSONResponse({"type": "error", "error": {"type": "rate_limit_error",
                                                            "message": "per-run model call limit reached"}}, status_code=429)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["authorization"] = f"Bearer {config.LITELLM_API_KEY}"
    headers["accept-encoding"] = "identity"
    headers["x-litellm-tags"] = _tags(run_id)
    url = f"{config.LITELLM_BASE_URL}/{path}"
    if request.url.query:
        url += "?" + request.url.query
    upstream = await client().send(client().build_request(request.method, url, headers=headers, content=body),
                                   stream=True)
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in DROP_RESPONSE}
    content_type = upstream.headers.get("content-type", "")

    if is_message and "text/event-stream" in content_type:
        meter = SSEUsage()

        async def stream():
            # finally runs on normal end and when the runner disconnects (cancelled run).
            try:
                async for chunk in upstream.aiter_raw():
                    meter.feed(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                db.record_call(run_id, model, path, upstream.status_code, meter.usage, _cost(upstream.headers))

        return StreamingResponse(stream(), status_code=upstream.status_code, headers=out_headers)

    data = await upstream.aread()
    await upstream.aclose()
    if is_message:
        usage = {}
        try:
            usage = json.loads(data).get("usage") or {}
        except ValueError:
            pass
        db.record_call(run_id, model, path, upstream.status_code, usage, _cost(upstream.headers))
    return Response(content=data, status_code=upstream.status_code, headers=out_headers)
