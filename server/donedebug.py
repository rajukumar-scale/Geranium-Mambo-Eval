"""The admin page's "done, not submitted" debugger: for each contributor who passed /rubric but hasn't submitted
the task, what happened since: is the task still theirs on the platform (or was it reassigned), did they keep
editing and rerun reviews (and did a rerun fail), did they ask for help or paste platform linter flags, or have
they gone quiet. Signals are hints for an admin, not verdicts.

Platform claims come from Snowflake through a saved Redash query (a few minutes behind); the rest is the
portal's own data.
"""

import json
import re
import time

from . import auth, config, db

ID = re.compile(r"^[0-9a-f]{24}$")
HELP_WORDS = re.compile(r"\?|\b(how|where|why|what|taxonomy|submit|stuck|help|error|can't|cannot|unable)\b", re.I)
_claims = {"at": 0.0, "key": None, "rows": None, "error": None}


async def _claim_rows(task_ids, node_ids, force=False):
    """Pending holders of these tasks plus the contributors' own nodes (cached 2 minutes)."""
    key = (tuple(task_ids), tuple(node_ids))
    if not force and _claims["key"] == key and time.time() - _claims["at"] < 120:
        return _claims["rows"], _claims["error"]
    rows, error = [], None
    if config.CLAIMS_QUERY_ID and task_ids:
        try:
            rows = await auth.redash_rows(config.CLAIMS_QUERY_ID, {"task_ids": ",".join(task_ids),
                                                                   "node_ids": ",".join(node_ids) or task_ids[0]})
            rows = [{k.lower(): v for k, v in r.items()} for r in rows]
        except Exception as err:  # noqa: BLE001 - the portal half still works
            error = f"Couldn't read platform claims: {str(err)[:150]}"
    _claims.update(at=time.time(), key=key, rows=rows, error=error)
    return rows, error


def _clip(text, n=220):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


async def diagnose(attempts, force=False):
    """attempts: the attempt ids the funnel counts as "done, not submitted"."""
    now = time.time()
    chats = [db.get_chat(a) for a in attempts]
    chats = [c for c in chats if c]
    tasks = sorted({c["task_id"] for c in chats if ID.match(c["task_id"] or "")})
    nodes = sorted({c["attempt_id"] for c in chats if ID.match(c["attempt_id"])})
    rows, claims_error = await _claim_rows(tasks, nodes, force)
    holders, own = {}, {}
    for r in rows:
        if r["kind"] == "holder":
            holders.setdefault(r["task_id"], []).append(r)
        else:
            own[r["node_id"]] = r
    out = []
    for chat in chats:
        a = chat["attempt_id"]
        passed_at = db.one("select max(p.created_at) at from proofs p join runs r on r.id = p.run_id "
                           "where p.attempt_id=? and coalesce(r.actor_admin, 0)=0 and json_extract(p.payload, '$.stage')=?",
                           (a, config.FINAL_STAGE))["at"] or 0
        msgs = db.q("select role, content, created_at from messages where attempt_id=? and created_at>? and role in ('user','admin') "
                    "order by id", (a, passed_at + 2))
        mine = [m for m in msgs if m["role"] == "user"]
        reruns = db.q("select command, kind, status, verdict, score, created_at from runs where attempt_id=? and created_at>? "
                      "and coalesce(actor_admin, 0)=0 order by created_at", (a, passed_at + 2))
        latest = {}
        for r in reruns:
            if r["kind"] == "review" and r["command"]:
                latest[r["command"]] = r
        failing = [f"/{c} {r['verdict']}" + (f" {r['score']}" if r["score"] is not None else "")
                   for c, r in latest.items() if r["verdict"] == "FAIL"]
        running = [f"/{c}" for c, r in latest.items() if r["status"] in ("queued", "running")]
        # the platform side
        held = [h for h in holders.get(chat["task_id"], []) if h["user_id"] == chat["user_id"]]
        others = [h for h in holders.get(chat["task_id"], []) if h["user_id"] != chat["user_id"]]
        node = own.get(a)
        if held:
            h = held[0]
            claim = {"state": "theirs", "since": h["created_at"], "touched": h["updated_at"]}
        elif others:
            claim = {"state": "reassigned", "to": others[0]["email"], "since": others[0]["created_at"]}
        elif node and node["status"] == "canceled":
            claim = {"state": "cancelled", "at": node["updated_at"]}
        else:
            claim = {"state": "unknown" if claims_error or not config.CLAIMS_QUERY_ID else "none"}
        # signals, most telling first
        signals = []
        hours = (now - passed_at) / 3600 if passed_at else None
        if claim["state"] == "reassigned":
            signals.append(("gone", f"Task was reassigned to {claim['to']}: it isn't theirs to submit any more"))
        elif claim["state"] in ("cancelled", "none"):
            signals.append(("gone", "Their claim on the task was cancelled: nothing to submit"))
        if failing:
            signals.append(("rework", f"Kept editing after the pass and a rerun now fails ({', '.join(failing)}): the pass no longer holds"))
        elif running:
            signals.append(("rework", f"Rerunning reviews after the pass ({', '.join(running)} running now)"))
        elif latest:
            signals.append(("rework", f"Kept editing after the pass; reruns still pass ({', '.join('/' + c for c in latest)})"))
        linter = [m for m in mine if "linter" in (m["content"] or "").lower()]
        if linter:
            signals.append(("blocked", "Pasted platform AI linter flags: the platform may be blocking the submit"))
        # a question is short; a long message is a pasted input (prompt, SQL, rubric), not someone asking for help
        asks = [m for m in mine if len(m["content"] or "") <= 300 and not (m["content"] or "").lstrip().startswith("/")
                and HELP_WORDS.search(m["content"] or "") and m not in linter]
        if asks:
            signals.append(("blocked", f"Asked: “{_clip(asks[-1]['content'], 140)}”"))
        if not mine and not latest and claim["state"] == "theirs":
            if hours is not None and hours < 1.5:
                signals.append(("fresh", "Just finished; probably submitting now"))
            else:
                signals.append(("quiet", f"Quiet since the pass ({hours:.0f} h) and still holding the task"))
        last = mine[-1] if mine else None
        out.append({
            "attempt_id": a, "task_id": chat["task_id"], "email": chat["email"],
            "passed_at": passed_at, "hours_since_pass": round(hours, 1) if hours is not None else None,
            "claim": claim, "messages_since": len(mine), "admin_replies_since": len(msgs) - len(mine),
            "reruns": [{"command": c, "verdict": r["verdict"], "score": r["score"], "status": r["status"], "at": r["created_at"]}
                       for c, r in latest.items()],
            "last_message": {"text": _clip(last["content"]), "at": last["created_at"]} if last else None,
            "signals": [{"kind": k, "text": t} for k, t in signals],
        })
    order = {"gone": 0, "rework": 1, "blocked": 2, "quiet": 3, "fresh": 4}
    out.sort(key=lambda d: (min((order[s["kind"]] for s in d["signals"]), default=5), -(d["hours_since_pass"] or 0)))
    return {"people": out, "claims_error": claims_error, "checked_at": now}
