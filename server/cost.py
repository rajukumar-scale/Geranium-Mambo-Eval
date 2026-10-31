"""The admin Cost page: what Atlas Commercial spends on models per day, per submitted task and per contributor.

Spend comes from LiteLLM's request log and SMIG's usage events (Snowflake, through saved Redash queries; since ~2026-10-16
most calls are routed through SMIG and never reach LiteLLM), on every key, for requests tagged with the project. The platform's task runs (Agent Runner, pre-check and verifier runs on the HG4 env, run IDs
task-snapshot-hg4-...) bill pub-mcp-commercial and the agent-env keys; a run counts for a task and contributor when its
run ID appears in one of their submitted attempts, or when it was launched from the task's pipeline node (aect-<node>,
counted for the node's current worker); re-runs and other unsubmitted work can't be attributed. The old in-task
sandbox (chat-lite-sandbox, on this portal's key) is tagged with task and contributor. The hint pipeline (autohint-...)
is shown on its own. Other teams' work that carries the project tag (coopbench, ckpteval...) is left out. This portal's
reviews come from its own run records, at SMIG's billed cost for runs the proxy tagged with their runId (from 2026-10-26
20:00 UTC) and at the list-price estimate before that; the page reports how many recent task runs have no logged spend.
A portal review counts for whoever ran it (its actor), or the chat's holder when the run has no actor.

A "submitted task" is a contributor's submission at review level -1 or 0 in the Atlas Commercial project (the bot
and @scale.com accounts excluded). Its cost is what the people who submitted it spent on it since it started, up to the
range's end (their task runs, in-task sandbox and portal reviews); what earlier holders spent is shown next to it as
"lost before".

The queries take about a minute, longer than a proxy waits, so one query returns every fact since 2026-08-01 (per day,
per task x contributor x day, and each submission). It's refreshed in the background every 15 minutes and kept on disk
across restarts; any date range is then cut from it at once.

Lost spend is counted per person and task (Evan, 2026-10-31). Each person's spend on a task is:
- submitted: they submitted that task at L-1/L0 at some point (also if they claimed it again later and submitted then,
  so their earlier spend fed the submission);
- in progress: they hold a live claim on it now (a pending L-1 node of theirs that hasn't run out, or a paused one);
- internal: an @scale.com account, an internal worker source (queue_manager, Scale API) or an admin's portal run;
- lost: anything else - their claim ended (released, expired, taken away, task cancelled) without them submitting,
  counted at once and even if someone else submits the task later. Why: "Task cancelled" when the task is cancelled,
  otherwise "Claim released or expired".
Claims come from the CB tracker's cached facts (L-1 nodes and incomplete attempts since 2026-10-01). With no node of
that person on the task (older work), a second, small query's task state stands in: they're in progress if the task
has a live claim, or is paused, and they're its last worker. Until the CB tracker's facts have loaded only that
fallback is used and the figures say they're partial. Lost spend is kept by the day it was spent.

The range's total splits into parts that add up to it exactly, in each view (task runs vs evals): spent by people who
submitted, lost, in progress, internal tests, and what can't be tied to a task (task runs never seen in a submitted
attempt, the hint pipeline, untagged in-task sandbox calls and portal runs with no task).

Tasks not submitted yet (by anyone), not cancelled, with spend in the range that 2 or more people have claimed are
listed on their own with their spend in the range, how much of it is lost and what the current holder spent. Claimers
are the distinct people on the task's L-1 claims and incomplete attempts in the CB tracker's cached facts (since
2026-10-01) plus the portal's chat holders on it (a re-served node overwrites its WORKER); internal accounts don't count.
"""
import asyncio
import calendar
import json
import logging
import statistics
import time

from . import auth, cbs, config, db, spend

log = logging.getLogger("portal.cost")
DAY = 86400
START = 1785542400  # 2026-08-01 00:00 UTC: the facts query starts here (task runs on the project's key start 08-10)
REFRESH_SEC = 900
RETRY_SEC = 120  # after a failed refresh, wait this long before trying again
LOOKUP_TIMEOUT = 300  # the query scans LiteLLM + SMIG; Redash can also queue it behind other work
COVERAGE_DAYS = 5  # how far back from the range's end the "task runs not logged" check looks
LOST_REASONS = {"cancelled": "Task cancelled", "released": "Claim released or expired"}
INTERNAL_SOURCES = ("queue_manager", "Scale API")
PARTS = ("submitted", "lost", "in_progress", "internal", "untied")  # the total, split (see the module docstring)
CACHE_FILE = config.DATA_DIR / "cost_facts.json"
_facts = {"at": 0.0, "rows": None, "billed": [], "states": [], "task": None, "error": None, "failed_at": 0.0}


def _rows(rows):
    return [{k.lower(): v for k, v in row.items()} for row in rows]


def _portal(days_start, billed=None, days_end=None):
    """This portal's run costs: per UTC day in the range and per contributor in it (and who was active each day), plus
    every run up to the range's end with its task and person (the actor, or the chat's holder when there's no actor;
    admins' runs are marked). A run SMIG billed (tagged with its runId) counts at that cost; older runs at the list-price
    estimate, which runs ~40% above what's billed. Also returns how much of the range is billed vs estimated."""
    stop = (days_end + DAY) if days_end is not None else float("inf")
    billed = billed or {}
    per_day, per_person, people_day, runs = {}, {}, {}, []
    basis = {"billed": 0.0, "estimated": 0.0}
    for run in db.q("select r.id, r.cost_estimate, r.models, r.created_at, coalesce(r.actor_admin, 0) by_admin, r.actor_user_id, r.actor_email, "
                    "c.task_id, c.user_id chat_user, c.email chat_email "
                    "from runs r left join chats c on c.attempt_id = r.attempt_id where coalesce(r.loadtest, 0) = 0"):
        usd = billed[run["id"]] if run["id"] in billed else spend.run_spend(run)
        if not usd or run["created_at"] >= stop:
            continue
        admin = bool(run["by_admin"])
        user = None if admin else (run["actor_user_id"] or run["chat_user"])
        email = ((run["actor_email"] if run["actor_user_id"] or admin else run["chat_email"]) or "").split("+outlier")[0].lower()
        runs.append({"at": run["created_at"], "usd": usd, "task": run["task_id"], "user": user, "email": email, "admin": admin})
        if run["created_at"] >= days_start:
            basis["billed" if run["id"] in billed else "estimated"] += usd
            day = int(run["created_at"] // DAY * DAY)
            per_day[day] = per_day.get(day, 0) + usd
            if user:
                per_person[user] = per_person.get(user, 0) + usd
                people_day.setdefault(day, set()).add(user)
    return per_day, per_person, people_day, basis, runs


def _period_start(day, period):
    """The UTC day that starts `day`'s week (Monday) or calendar month."""
    if period == "week":
        return day - time.gmtime(day).tm_wday * DAY
    if period == "month":
        t = time.gmtime(day)
        return calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0))
    return day


def _period_end(start, period):
    """The first day after the week or month that starts on `start`."""
    if period == "week":
        return start + 7 * DAY
    t = time.gmtime(start)
    return calendar.timegm((t.tm_year + t.tm_mon // 12, t.tm_mon % 12 + 1, 1, 0, 0, 0))


def _claims(facts):
    """From the CB tracker's facts: people {id: user row}, L-1 nodes per (task, person), when each person's incomplete
    attempts on a task were made, and their L-1 submits."""
    users, nodes, incomplete, submits = {}, {}, {}, set()
    for f in facts or ():
        f = {k.lower(): v for k, v in f.items()}
        key = (f.get("task"), f.get("worker"))
        if f["kind"] == "user" and f.get("worker"):
            users[f["worker"]] = f
        elif f["kind"] == "node" and f.get("worker"):
            nodes.setdefault(key, []).append(f)
        elif f["kind"] == "incomplete" and f.get("worker"):
            incomplete[key] = max(incomplete.get(key, 0), int(f.get("t1") or 0))
        elif f["kind"] == "submit" and f.get("worker"):
            submits.add(key)
    return users, nodes, incomplete, submits


def _spend_by_claim(tcd, portal_runs, all_subs, states, claim_facts, start, stop, now):
    """Each person's spend on each task, classified (see the module docstring): {(task, person id): row}, plus what
    can't be tied to a task or is an admin's test (in the range). A row has the range's spend (agent_runs / sandbox /
    portal, and per day), everything up to the range's end ("upto"), its status and, when lost, why and when the claim
    ended. Person id "" = a portal run on a task with no actor and no chat holder."""
    num = lambda r, k: float(r.get(k) or 0)  # noqa: E731
    state = {r["task"]: r for r in _rows(states or ()) if r.get("task")}
    users, nodes, incomplete, cb_submits = _claims(claim_facts)
    submitted = {(r["task"], r["cb"]) for r in all_subs} | cb_submits
    pairs, emails = {}, {}
    other = {"untied_runs": 0.0, "untied_sandbox": 0.0, "untied_portal": 0.0, "admin_portal": 0.0}

    def pair(task, uid, email):
        p = pairs.setdefault((task, uid), {"task_id": task, "user_id": uid, "agent_runs": 0.0, "sandbox": 0.0, "portal": 0.0,
                                            "upto": [0.0, 0.0, 0.0], "days": {}, "last": 0, "seen": 0})
        if email:
            emails.setdefault(uid, set()).add(email.lower())
        return p
    for r in tcd:
        t = int(r["t"])
        if t >= stop:
            continue
        runs, sandbox = num(r, "runs"), num(r, "sandbox")
        if not r.get("task"):  # a contributor's sandbox call with no task on it
            if t >= start:
                other["untied_runs"] += runs
                other["untied_sandbox"] += sandbox
            continue
        p = pair(r["task"], r.get("cb") or "", r.get("email"))
        p["upto"][0] += runs
        p["upto"][1] += sandbox
        p["last"] = max(p["last"], min(t + DAY, int(now)))  # a day's spend could be as late as its end
        p["seen"] = max(p["seen"], t)  # the day itself, for "last seen"
        if t >= start:
            p["agent_runs"] += runs
            p["sandbox"] += sandbox
            day = p["days"].setdefault(t, [0.0, 0.0])
            day[0] += runs
            day[1] += sandbox
    for run in portal_runs:
        at, usd = run["at"], run["usd"]
        if run["admin"] or not run["task"]:
            if at >= start:
                other["admin_portal" if run["admin"] else "untied_portal"] += usd
            continue
        p = pair(run["task"], run["user"] or "", run["email"])
        p["upto"][2] += usd
        p["last"] = max(p["last"], int(at))
        p["seen"] = max(p["seen"], int(at))
        if at >= start:
            p["portal"] += usd
            p["days"].setdefault(int(at // DAY * DAY), [0.0, 0.0])[1] += usd

    def live(task, uid, email):
        mine = nodes.get((task, uid))
        if mine:  # their L-1 nodes on it: a pending one that hasn't run out, or a paused one
            return any(n.get("status") == "paused" or (n.get("status") == "pending" and int(n.get("t2") or 0) > now) for n in mine)
        s = state.get(task) or {}  # no node of theirs on record: the task's state, if they're its last worker
        held = int(s.get("live_claim") or 0) or (int(s.get("paused") or 0) and not int(s.get("pending") or 0))
        return bool(held) and (not uid or s.get("last_worker") == uid or bool(email and s.get("last_worker_email") == email))

    for (task, uid), p in pairs.items():
        u = users.get(uid) or {}
        mine = emails.get(uid, set()) | ({u["email"].split("+outlier")[0].lower()} if u.get("email") else set())
        email = sorted(mine, key=lambda e: ("+" in e, e))[0] if mine else ""
        s = state.get(task) or {}
        p["email"] = email
        if uid and (u.get("source") in INTERNAL_SOURCES or any("@scale.com" in e for e in mine)):
            p["status"] = "internal"
        elif uid and (task, uid) in submitted:
            p["status"] = "submitted"
        elif live(task, uid, email):
            p["status"] = "in_progress"
        else:
            p["status"] = "lost"
            p["reason"] = "cancelled" if (s.get("task_status") or "") == "canceled" else "released"
            expired = [int(n["t2"]) for n in nodes.get((task, uid), ()) if n.get("t2") and int(n["t2"]) <= now]
            if incomplete.get((task, uid)):
                p["ended"], p["ended_how"] = incomplete[(task, uid)], "ended"
            elif expired:
                p["ended"], p["ended_how"] = max(expired), "expired"
            else:
                p["ended"], p["ended_how"] = p["seen"], "last seen"
    return pairs, other


def _claim_sources(task_ids):
    """What _claimers counts from: the CB tracker's cached Snowflake facts (None until they've loaded) and the portal's
    chats on these tasks."""
    marks = ",".join("?" * len(task_ids))
    return cbs._facts["rows"], db.q(f"select task_id, user_id, email from chats where task_id in ({marks}) and user_id is not null", tuple(task_ids))


def _claimers(task_ids, facts, chats):
    """The distinct people who claimed each task: WORKERs on its L-1 claims (node rows) and incomplete attempts, plus the
    portal's chat holders (a re-served node overwrites its WORKER). Internal accounts are left out, as on the CB tracker.
    {task: {user id: email}}; facts None (not loaded yet) leaves chats only."""
    wanted, users, seen = set(task_ids), {}, {}
    rows = [{k.lower(): v for k, v in f.items()} for f in facts or ()]
    for f in rows:
        if f["kind"] == "user" and f.get("worker"):
            users[f["worker"]] = f
        elif f["kind"] in ("node", "incomplete") and f.get("task") in wanted and f.get("worker"):
            seen.setdefault(f["task"], {}).setdefault(f["worker"], "")
    for c in chats:
        if c["task_id"] in wanted and c["user_id"]:
            who = seen.setdefault(c["task_id"], {})
            who[c["user_id"]] = who.get(c["user_id"]) or c["email"] or ""
    out = {}
    for task, who in seen.items():
        for uid, email in who.items():
            u = users.get(uid) or {}
            email = (u.get("email") or email or "").split("+outlier")[0]
            if u.get("source") not in ("queue_manager", "Scale API") and "@scale.com" not in email.lower():
                out.setdefault(task, {})[uid] = email
    return out


def build(facts, start, end, billed_rows=(), days=None, states=None):
    """The report for UTC days start..end (epoch seconds of each day's midnight), cut from the facts query's rows."""
    now = time.time()
    today = int(now // DAY * DAY)
    billed = {r["run_id"]: float(r.get("cost_usd") or 0) for r in _rows(billed_rows) if r.get("run_id")}
    portal_day, portal_person, portal_people, portal_basis, portal_runs = _portal(start, billed, end)
    rows = _rows(facts)
    num = lambda r, k: float(r.get(k) or 0)  # noqa: E731
    stop = end + DAY
    logged = {int(r["t"]): r for r in rows if r["kind"] == "day"}
    tcd = [r for r in rows if r["kind"] == "tcd"]
    all_subs = [r for r in rows if r["kind"] == "sub"]
    subs = [r for r in all_subs if start <= int(r["t"]) < stop]
    sub_day = {}
    for r in subs:
        d = sub_day.setdefault(int(r["t"]) // DAY * DAY, ({}, set()))
        d[0][r["task"]] = 1
        d[1].add(r["cb"])

    # Each person's spend on each task, classified (module docstring). Lost spend, the breakdown and the "2+ claimers"
    # list need each task's platform state: None = that query isn't set up, so the page leaves them out.
    state = {r["task"]: r for r in _rows(states or ()) if r.get("task")}
    task_submitted = {r["task"] for r in all_subs} | {t for t, r in state.items() if int(r.get("submitted") or 0)}
    cancelled = {t for t, r in state.items() if (r.get("task_status") or "") == "canceled"}
    spent_on = ({r["task"] for r in tcd if r.get("task") and start <= int(r["t"]) < stop}
                | {x["task"] for x in portal_runs if x["task"] and not x["admin"] and start <= x["at"] < stop})
    candidates = sorted(spent_on - task_submitted - cancelled) if states is not None else []
    claim_facts, chats = _claim_sources(candidates) if candidates else (cbs._facts["rows"], [])
    pairs, other = _spend_by_claim(tcd, portal_runs, all_subs, states, claim_facts, start, stop, now)
    by_task_pairs = {}
    for p in pairs.values():
        p["evals"] = p["sandbox"] + p["portal"]
        p["total"] = p["agent_runs"] + p["evals"]
        by_task_pairs.setdefault(p["task_id"], []).append(p)
    lost_day = {}
    for p in pairs.values():
        if p["status"] == "lost":
            for d, (runs_usd, evals_usd) in p["days"].items():
                day = lost_day.setdefault(d, [0.0, 0.0])
                day[0] += runs_usd
                day[1] += evals_usd
    if states is None:
        lost_day = {}

    daily = []
    for d in range(start, stop, DAY):
        r = logged.get(d, {})
        runs, linked, sandbox, hint, portal = num(r, "runs"), num(r, "runs_linked"), num(r, "sandbox"), num(r, "hint"), portal_day.get(d, 0)
        people = {c for c in (r.get("cb_ids") or "").split(",") if c} | portal_people.get(d, set())
        day_tasks, day_submitters = sub_day.get(d, ({}, set()))
        lost_runs, lost_evals = lost_day.get(d, (0.0, 0.0))
        daily.append({"day": d, "today": d == today, "runs": runs, "runs_linked": linked, "sandbox": sandbox, "hint": hint,
                      "portal": round(portal, 2), "task_runs": round(runs + sandbox, 2), "total": round(runs + sandbox + hint + portal, 2),
                      "requests": int(r.get("requests") or 0), "active_cbs": len(people), "people": people,
                      "tasks_touched": int(r.get("tasks_touched") or 0), "tasks_submitted": len(day_tasks),
                      "submitters": len(day_submitters), "sub_runs": int(r.get("sub_runs") or 0), "sub_runs_logged": int(r.get("sub_runs_logged") or 0),
                      "lost_runs": round(lost_runs, 2), "lost_evals": round(lost_evals, 2)})

    # tasks submitted in the range; their cost is what the people who submitted them spent on them up to the range's end,
    # and what earlier holders spent that's now lost is shown next to it (Evan, 2026-10-31)
    by_task = {}
    for r in subs:
        t = by_task.setdefault(r["task"], {"last": 0, "n": 0, "who": set(), "ids": set()})
        t["last"], t["n"] = max(t["last"], int(r["t"])), t["n"] + 1
        t["who"].add(r.get("email") or "unknown")
        t["ids"].add(r["cb"])
    task_spend = {}
    for r in tcd:
        if r.get("task") in by_task and int(r["t"]) < stop:
            p = task_spend.setdefault(r["task"], {"requests": 0, "cbs": set(), "first": None})
            p["requests"] += int(r.get("requests") or 0)
            if r.get("cb"):
                p["cbs"].add(r["cb"])
            p["first"] = min(p["first"] or int(r["t"]), int(r["t"]))
    tasks = []
    for task_id, t in by_task.items():
        p = task_spend.get(task_id, {"requests": 0, "cbs": set(), "first": None})
        mine = by_task_pairs.get(task_id, [])
        upto = lambda status, i: sum(x["upto"][i] for x in mine if x["status"] == status)  # noqa: E731
        runs_usd, sandbox_usd, reviews_usd = upto("submitted", 0), upto("submitted", 1), upto("submitted", 2)
        lost_runs, lost_evals = upto("lost", 0), upto("lost", 1) + upto("lost", 2)
        tasks.append({"task_id": task_id, "last_submitted": t["last"], "submissions": t["n"], "submitters": ", ".join(sorted(t["who"])),
                      "task_runs": round(runs_usd + sandbox_usd, 2), "agent_runs": round(runs_usd, 2), "sandbox": round(sandbox_usd, 2),
                      "portal": round(reviews_usd, 2), "total": round(runs_usd + sandbox_usd + reviews_usd, 2),
                      "lost_before_agent_runs": round(lost_runs, 2), "lost_before_evals": round(lost_evals, 2),
                      "lost_before": round(lost_runs + lost_evals, 2),
                      "requests": p["requests"], "contributors": len(p["cbs"]), "first_run": p["first"]})
    tasks.sort(key=lambda t: -t["total"])

    # contributors: what's tied to them in the range, and the tasks they submitted in it
    by_person = {}
    def person(cb, email):
        return by_person.setdefault(cb, {"email": email or "unknown", "runs": 0.0, "sandbox": 0.0, "days": set(), "tasks": set(), "requests": 0, "submitted": set()})
    for r in tcd:
        if r.get("cb") and start <= int(r["t"]) < stop:
            p = person(r["cb"], r.get("email"))
            p["runs"] += num(r, "runs")
            p["sandbox"] += num(r, "sandbox")
            p["days"].add(int(r["t"]))
            p["requests"] += int(r.get("requests") or 0)
            if r.get("task"):
                p["tasks"].add(r["task"])
    for r in subs:
        person(r["cb"], r.get("email"))["submitted"].add(r["task"])
    for x in pairs.values():  # people who only ran portal reviews in the range
        if x["user_id"] and x["user_id"] in portal_person and x["user_id"] not in by_person:
            person(x["user_id"], x["email"])
    lost_person = {}
    for x in pairs.values():
        if x["status"] == "lost" and x["user_id"]:
            mine = lost_person.setdefault(x["user_id"], [0.0, 0.0])
            mine[0] += x["agent_runs"]
            mine[1] += x["evals"]
    people = []
    for cb, p in by_person.items():
        if "@scale.com" in p["email"]:
            continue
        runs_usd, reviews_usd = round(p["runs"] + p["sandbox"], 2), round(portal_person.get(cb, 0), 2)
        submitted, active = len(p["submitted"]), len(p["days"])
        lost_runs, lost_evals = lost_person.get(cb, (0.0, 0.0)) if states is not None else (0.0, 0.0)
        people.append({"user_id": cb, "email": p["email"], "task_runs": runs_usd, "agent_runs": round(p["runs"], 2), "sandbox": round(p["sandbox"], 2),
                       "portal": reviews_usd, "total": round(runs_usd + reviews_usd, 2), "active_days": active, "tasks_touched": len(p["tasks"]),
                       "tasks_submitted": submitted, "requests": p["requests"],
                       "lost_agent_runs": round(lost_runs, 2), "lost_evals": round(lost_evals, 2), "lost": round(lost_runs + lost_evals, 2),
                       "per_task": round((runs_usd + reviews_usd) / submitted, 2) if submitted else None,
                       "per_active_day": round(runs_usd / active, 2) if active else None})
    people.sort(key=lambda p: -p["total"])

    # the same days bucketed into UTC weeks (Monday start) and calendar months, clipped to the range
    periods = {}
    for period in ("week", "month"):
        out = {}
        for d in daily:
            b0 = _period_start(d["day"], period)
            b = out.setdefault(b0, {"day": b0, "runs": 0.0, "runs_linked": 0.0, "sandbox": 0.0, "hint": 0.0, "portal": 0.0,
                                    "lost_runs": 0.0, "lost_evals": 0.0, "people": set(),
                                    "tasks": set(), "cb_days": 0, "partial_start": b0 < start,
                                    "partial_end": _period_end(b0, period) > stop, "today": False})
            for k in ("runs", "runs_linked", "sandbox", "hint", "portal", "lost_runs", "lost_evals"):
                b[k] += d[k]
            b["people"] |= d["people"]
            b["cb_days"] += len(d["people"])
            b["today"] = b["today"] or d["today"]
        for r in subs:
            out[_period_start(int(r["t"]) // DAY * DAY, period)]["tasks"].add(r["task"])
        periods[period] = [{**{k: v for k, v in b.items() if k not in ("people", "tasks")},
                            **{k: round(b[k], 2) for k in ("runs", "runs_linked", "sandbox", "hint", "portal", "lost_runs", "lost_evals")},
                            "total": round(b["runs"] + b["sandbox"] + b["hint"] + b["portal"], 2),
                            "active_cbs": len(b["people"]), "tasks_submitted": len(b["tasks"])} for _, b in sorted(out.items())]
    for d in daily:
        d.pop("people")

    add = lambda key: round(sum(d[key] for d in daily), 2)  # noqa: E731
    cb_days = sum(d["active_cbs"] for d in daily)
    task_totals = [t["total"] for t in tasks]
    recent = [d for d in daily if d["day"] > end - COVERAGE_DAYS * DAY]
    sub_runs, sub_logged = sum(d["sub_runs"] for d in recent), sum(d["sub_runs_logged"] for d in recent)
    # The total, split so the parts add up to it exactly in each view: task runs = runs + hint pipeline, evals = in-task
    # sandbox + portal reviews. "Can't be tied to a task" takes what's left on each side (task runs never seen in a
    # submitted attempt, the hint pipeline, untagged sandbox calls, portal runs with no task, and rounding).
    runs_total = round(sum(d["runs"] + d["hint"] for d in daily), 2)
    evals_total = round(sum(d["sandbox"] + d["portal"] for d in daily), 2)
    total = round(runs_total + evals_total, 2)
    breakdown = None
    if states is not None:
        acc = {k: [0.0, 0.0] for k in PARTS}
        counts = {k: 0 for k in PARTS[:-1]}
        for x in pairs.values():
            acc[x["status"]][0] += x["agent_runs"]
            acc[x["status"]][1] += x["evals"]
            if x["total"] > 0:
                counts[x["status"]] += 1
        acc["internal"][1] += other["admin_portal"]
        breakdown = {k: {"agent_runs": round(acc[k][0], 2), "evals": round(acc[k][1], 2)} for k in PARTS[:-1]}
        breakdown["untied"] = {"agent_runs": round(runs_total - sum(breakdown[k]["agent_runs"] for k in PARTS[:-1]), 2),
                               "evals": round(evals_total - sum(breakdown[k]["evals"] for k in PARTS[:-1]), 2),
                               "hint": add("hint"), "portal_no_task": round(other["untied_portal"], 2)}
        breakdown["internal"]["admin_portal"] = round(other["admin_portal"], 2)
        breakdown["total"] = {"agent_runs": runs_total, "evals": evals_total, "all": total}
        breakdown["claims"] = counts  # people x tasks with spend in the range, per part
        breakdown["partial"] = claim_facts is None

    lost = pending = None
    if states is not None:
        claims = []
        held_tasks = {x["task_id"] for x in pairs.values() if x["status"] == "in_progress"}
        def task_now(t, s):  # where the task stands now, for the lost-per-task table (Evan, 2026-10-31)
            if t in task_submitted:
                return "Submitted"
            if t in held_tasks or int(s.get("live_claim") or 0):
                return "In progress"
            return "Cancelled" if (s.get("task_status") or "") == "canceled" else "Not submitted"
        for x in pairs.values():
            if x["status"] != "lost" or x["total"] <= 0:
                continue
            s = state.get(x["task_id"]) or {}
            claims.append({"task_id": x["task_id"], "task_now": task_now(x["task_id"], s), "user_id": x["user_id"], "email": x["email"] or "unknown", "reason": x["reason"],
                           "reason_label": LOST_REASONS[x["reason"]], "cancel_reason": s.get("cancel_reason") or "",
                           "ended": x["ended"], "ended_how": x["ended_how"],
                           "agent_runs": round(x["agent_runs"], 2), "sandbox": round(x["sandbox"], 2), "portal": round(x["portal"], 2),
                           "evals": round(x["evals"], 2), "total": round(x["total"], 2)})
        claims.sort(key=lambda c: -c["total"])
        reasons = []
        for key, label in LOST_REASONS.items():
            mine = [c for c in claims if c["reason"] == key]
            if mine:
                reasons.append({"reason": key, "label": label, "claims": len(mine), "tasks": len({c["task_id"] for c in mine}),
                                "agent_runs": round(sum(c["agent_runs"] for c in mine), 2), "evals": round(sum(c["evals"] for c in mine), 2)})
        held = [x for x in pairs.values() if x["status"] == "in_progress" and x["total"] > 0]
        in_progress = {"claims": len(held), "tasks": len({x["task_id"] for x in held}), "agent_runs": breakdown["in_progress"]["agent_runs"],
                       "evals": breakdown["in_progress"]["evals"]}
        lost = {"agent_runs": breakdown["lost"]["agent_runs"], "evals": breakdown["lost"]["evals"], "claims": claims, "reasons": reasons,
                "in_progress": in_progress, "partial": claim_facts is None}

        # tasks nobody has submitted yet, not cancelled, that 2+ people have claimed (Rafael, 2026-10-31), with what's lost
        # on them so far and what the current holder spent (in the range)
        who = _claimers(candidates, claim_facts, chats)
        multi = []
        for task_id in candidates:
            mine = [x for x in by_task_pairs.get(task_id, []) if x["status"] != "internal" and x["total"] > 0]
            people_on = who.get(task_id) or {}
            if not mine or len(people_on) < 2:
                continue
            s = state.get(task_id) or {}
            part = lambda status, k: sum(x[k] for x in mine if x["status"] == status)  # noqa: E731
            holders = sorted({x["email"] or "unknown" for x in mine if x["status"] == "in_progress"})
            runs_usd, sandbox_usd, portal_usd = (sum(x[k] for x in mine) for k in ("agent_runs", "sandbox", "portal"))
            multi.append({"task_id": task_id, "holder": ", ".join(holders) or s.get("last_worker_email") or "",
                          "live_claim": bool(holders) or bool(int(s.get("live_claim") or 0)),
                          "contributors": ", ".join(sorted({x["email"] for x in mine if x["email"]})),
                          "last_activity": max([x["last"] for x in mine] + [int(s.get("last_activity") or 0)]),
                          "agent_runs": round(runs_usd, 2), "sandbox": round(sandbox_usd, 2), "portal": round(portal_usd, 2),
                          "total": round(runs_usd + sandbox_usd + portal_usd, 2),
                          "lost_agent_runs": round(part("lost", "agent_runs"), 2), "lost_evals": round(part("lost", "evals"), 2),
                          "lost": round(part("lost", "total"), 2),
                          "holder_agent_runs": round(part("in_progress", "agent_runs"), 2), "holder_evals": round(part("in_progress", "evals"), 2),
                          "holder_spend": round(part("in_progress", "total"), 2),
                          "claimers": len(people_on), "claimed_by": ", ".join(sorted(e or u for u, e in people_on.items()))})
        multi.sort(key=lambda t: -t["total"])
        pending = {"tasks": multi, "count": len(multi), "agent_runs": round(sum(t["agent_runs"] for t in multi), 2),
                   "evals": round(sum(t["sandbox"] + t["portal"] for t in multi), 2), "partial": claim_facts is None}

    return {
        "days": days, "start": start, "end": end, "today": today,
        "summary": {
            "total": total, "task_runs": add("task_runs"), "runs": add("runs"), "runs_linked": add("runs_linked"),
            "unlinked": round(add("runs") - add("runs_linked"), 2), "sandbox": add("sandbox"), "hint": add("hint"), "portal": add("portal"),
            "per_cb_day": round((add("task_runs") + add("portal")) / cb_days, 2) if cb_days else None,
            "tasks_submitted": len(tasks), "submitters": len({s for t in tasks for s in t["submitters"].split(", ") if s}),
            "per_task_median": round(statistics.median(task_totals), 2) if task_totals else None,
            "per_task_mean": round(statistics.fmean(task_totals), 2) if task_totals else None,
            "per_task_all_in": round(total / len(tasks), 2) if tasks else None,
            "active_cbs": sum(1 for p in people if p["task_runs"] > 0),
            "portal_billed": round(portal_basis["billed"], 2), "portal_estimated": round(portal_basis["estimated"], 2),
        },
        # How many task runs in the range's last few days' submissions have any spend in LiteLLM's log or SMIG: low = both miss them.
        "coverage": {"days": COVERAGE_DAYS, "runs": sub_runs, "logged": sub_logged,
                     "pct": round(100 * sub_logged / sub_runs) if sub_runs else None},
        "daily": daily, "weeks": periods["week"], "months": periods["month"], "tasks": tasks, "people": people,
        # the total in five parts that add up to it (None when the task-state query isn't set up; partial = the CB
        # tracker's claims haven't loaded, so only the task-state fallback was used)
        "breakdown": breakdown,
        # spend whose person's claim ended without them submitting: totals, per person x task, why, and the in-progress
        # total (None as above)
        "lost": lost,
        # tasks not submitted yet, not cancelled, claimed by 2+ people, with their spend in the range (None as above;
        # partial = the CB tracker's claims haven't loaded, so only portal chats were counted)
        "pending_multi": pending,
        "queries": {"facts": f"{config.REDASH_URL}/queries/{config.COST_FACTS_QUERY_ID}",
                    "states": f"{config.REDASH_URL}/queries/{config.COST_TASK_STATES_QUERY_ID}" if config.COST_TASK_STATES_QUERY_ID else ""},
    }


def span(days=None, start=None, end=None):
    """The UTC day range to show: the last `days` days, or start..end ('YYYY-MM-DD'), kept within Aug 1..today."""
    today = int(time.time() // DAY * DAY)
    day = lambda text: calendar.timegm(time.strptime(text, "%Y-%m-%d"))  # noqa: E731
    try:
        lo, hi = (day(start), day(end)) if start and end else (today - (int(days or 14) - 1) * DAY, today)
    except ValueError:
        lo, hi = today - 13 * DAY, today
    hi = min(max(hi, START), today)
    return min(max(lo, START), hi), hi


async def report(days=None, start=None, end=None, force=False):
    """The cost report for a range, cut from the cached facts at once. With nothing cached yet (first start) the page
    gets {"loading": true} and asks again; Refresh starts a fresh lookup and shows the current figures meanwhile."""
    if force or not _facts["rows"]:
        kick(force=force)
    if not _facts["rows"]:
        return {"error": _facts["error"]} if _facts["error"] else {"loading": True}
    lo, hi = span(days, start, end)
    data = build(_facts["rows"], lo, hi, _facts["billed"], days=None if start and end else int(days or 14),
                 states=_facts["states"] if config.COST_TASK_STATES_QUERY_ID else None)
    data.update(fetched_at=_facts["at"], refreshing=bool(_facts["task"]))
    if _facts["error"]:
        data["stale"] = _facts["error"]
    return data


def kick(force=False):
    """Start a background refresh unless one is running (or one just failed, unless forced)."""
    if _facts["task"] or (not force and time.time() - _facts["failed_at"] < RETRY_SEC):
        return
    _facts["task"] = asyncio.create_task(_refresh())


async def _refresh():
    try:
        billed_lookup = (auth.redash_rows(config.COST_PORTAL_SMIG_QUERY_ID, timeout=LOOKUP_TIMEOUT)
                         if config.COST_PORTAL_SMIG_QUERY_ID else asyncio.sleep(0, result=[]))
        states_lookup = (auth.redash_rows(config.COST_TASK_STATES_QUERY_ID, timeout=LOOKUP_TIMEOUT)
                         if config.COST_TASK_STATES_QUERY_ID else asyncio.sleep(0, result=[]))
        rows, billed, states = await asyncio.gather(auth.redash_rows(config.COST_FACTS_QUERY_ID, timeout=LOOKUP_TIMEOUT),
                                                    billed_lookup, states_lookup, return_exceptions=True)
        if isinstance(rows, Exception):
            raise rows
        if isinstance(billed, Exception):  # optional: portal reviews fall back to the estimate
            log.warning("portal SMIG cost lookup failed: %s", billed)
            billed = _facts["billed"]
        if isinstance(states, Exception):  # optional: lost spend keeps the last task states
            log.warning("task state lookup failed: %s", states)
            states = _facts["states"]
        _facts.update(at=time.time(), rows=rows, billed=billed, states=states, error=None, failed_at=0.0)
        _save()
    except Exception as error:  # noqa: BLE001 - the page keeps the last figures and says so
        log.warning("cost lookup failed: %s", error)
        _facts.update(error=f"Couldn't read the cost data: {str(error)[:200]}", failed_at=time.time())
    finally:
        _facts["task"] = None


def _save():
    try:
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": _facts["at"], "rows": _facts["rows"], "billed": _facts["billed"], "states": _facts["states"]}))
        tmp.replace(CACHE_FILE)
    except OSError as error:
        log.warning("couldn't save the cost cache: %s", error)


def restore():
    """Load the last saved facts, so a restart serves figures at once (refreshed in the background if old)."""
    try:
        saved = json.loads(CACHE_FILE.read_text())
        _facts.update(at=float(saved["at"]), rows=saved["rows"], billed=saved.get("billed") or [], states=saved.get("states") or [])
    except (OSError, ValueError, KeyError, TypeError):
        pass


async def loop():
    """Keep the facts fresh: restore the saved copy, then refresh whenever it's older than REFRESH_SEC."""
    restore()
    while True:
        if config.COST_FACTS_QUERY_ID and time.time() - _facts["at"] >= REFRESH_SEC:
            kick()
        await asyncio.sleep(60)
