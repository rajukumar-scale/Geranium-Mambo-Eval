"""The admin Cost page: what Atlas Commercial spends on models per day, per submitted task and per contributor.

Spend comes from LiteLLM's request log and SMIG's usage events (Snowflake, through saved Redash queries; since ~2026-10-16
most calls are routed through SMIG and never reach LiteLLM), on every key, for requests tagged with the project. The platform's task runs (Agent Runner, pre-check and verifier runs on the HG4 env, run IDs
task-snapshot-hg4-...) bill pub-mcp-commercial and the agent-env keys; a run counts for a task and contributor when its
run ID appears in one of their submitted attempts (re-runs and unsubmitted work can't be attributed). The old in-task
sandbox (chat-lite-sandbox, on this portal's key) is tagged with task and contributor. The hint pipeline (autohint-...)
is shown on its own. Other teams' work that carries the project tag (coopbench, ckpteval...) is left out. This portal's
reviews come from its own run records, at SMIG's billed cost for runs the proxy tagged with their runId (from 2026-10-26
20:00 UTC) and at the list-price estimate before that; the page reports how many recent task runs have no logged spend.

A "submitted task" is a contributor's submission at review level -1 or 0 in the Atlas Commercial project (the bot
and @scale.com accounts excluded). Its cost is its attributable task runs since it started, plus the portal reviews.
"""

import asyncio
import logging
import statistics
import time

from . import auth, config, db, spend

log = logging.getLogger("portal.cost")
DAY = 86400
RANGES = (7, 14, 30)
CACHE_SEC = 600
LOOKUP_TIMEOUT = 300  # the cost queries scan LiteLLM + SMIG; Redash can also queue them behind other work
COVERAGE_DAYS = 5  # how far back the "task runs not logged" check looks
_cache = {}  # days -> (fetched at, report)


def _rows(rows):
    return [{k.lower(): v for k, v in row.items()} for row in rows]


def _portal(days_start, billed=None):
    """This portal's run costs: per UTC day in the range (and who ran them), per task (all time) and per contributor
    (in the range). A run SMIG billed (tagged with its runId) counts at that cost; older runs at the list-price estimate,
    which runs ~40% above what's billed. Also returns how much of the range is billed vs estimated."""
    billed = billed or {}
    per_day, per_task, per_person, people_day = {}, {}, {}, {}
    basis = {"billed": 0.0, "estimated": 0.0}
    for run in db.q("select r.id, r.cost_estimate, r.models, r.created_at, coalesce(r.actor_admin, 0) by_admin, r.actor_user_id, c.task_id "
                    "from runs r left join chats c on c.attempt_id = r.attempt_id where coalesce(r.loadtest, 0) = 0"):
        usd = billed[run["id"]] if run["id"] in billed else spend.run_spend(run)
        if not usd:
            continue
        if run["created_at"] >= days_start:
            basis["billed" if run["id"] in billed else "estimated"] += usd
        per_task[run["task_id"]] = per_task.get(run["task_id"], 0) + usd
        if run["created_at"] >= days_start:
            day = int(run["created_at"] // DAY * DAY)
            per_day[day] = per_day.get(day, 0) + usd
            if not run["by_admin"] and run["actor_user_id"]:
                per_person[run["actor_user_id"]] = per_person.get(run["actor_user_id"], 0) + usd
                people_day.setdefault(day, set()).add(run["actor_user_id"])
    return per_day, per_task, per_person, people_day, basis


def build(days, day_rows, task_rows, person_rows, billed_rows=()):
    today = int(time.time() // DAY * DAY)
    start = today - (days - 1) * DAY
    billed = {r["run_id"]: float(r.get("cost_usd") or 0) for r in _rows(billed_rows) if r.get("run_id")}
    portal_day, portal_task, portal_person, portal_people, portal_basis = _portal(start, billed)
    logged = {int(r["day"] or 0): r for r in _rows(day_rows)}
    num = lambda r, k: float(r.get(k) or 0)  # noqa: E731

    daily = []
    for d in range(start, today + DAY, DAY):
        r = logged.get(d, {})
        runs, linked, sandbox, hint, portal = num(r, "runs"), num(r, "runs_linked"), num(r, "sandbox"), num(r, "hint"), portal_day.get(d, 0)
        active = {c for c in (r.get("cb_ids") or "").split(",") if c} | portal_people.get(d, set())
        daily.append({"day": d, "today": d == today, "runs": runs, "runs_linked": linked, "sandbox": sandbox, "hint": hint,
                      "portal": round(portal, 2), "task_runs": round(runs + sandbox, 2), "total": round(runs + sandbox + hint + portal, 2),
                      "requests": int(r.get("requests") or 0), "active_cbs": len(active),
                      "tasks_touched": int(r.get("tasks_touched") or 0), "tasks_submitted": int(r.get("tasks_submitted") or 0),
                      "submitters": int(r.get("submitters") or 0),
                      "sub_runs": int(r.get("sub_runs") or 0), "sub_runs_logged": int(r.get("sub_runs_logged") or 0)})

    tasks = []
    for r in _rows(task_rows):
        runs_usd, reviews_usd = num(r, "task_run_spend"), round(portal_task.get(r["task_id"], 0), 2)
        tasks.append({"task_id": r["task_id"], "last_submitted": r.get("last_submitted"), "submissions": int(r.get("submissions") or 0),
                      "submitters": r.get("submitters") or "", "task_runs": runs_usd, "agent_runs": num(r, "agent_runs"),
                      "sandbox": num(r, "sandbox"), "portal": reviews_usd, "total": round(runs_usd + reviews_usd, 2),
                      "requests": int(r.get("requests") or 0), "contributors": int(r.get("contributors") or 0), "first_run": r.get("first_run")})
    tasks.sort(key=lambda t: -t["total"])

    people = []
    for r in _rows(person_rows):
        runs_usd, reviews_usd = num(r, "task_run_spend"), round(portal_person.get(r["user_id"], 0), 2)
        submitted, active = int(r.get("tasks_submitted") or 0), int(r.get("active_days") or 0)
        people.append({"user_id": r["user_id"], "email": r.get("email") or "unknown", "task_runs": runs_usd,
                       "agent_runs": num(r, "agent_runs"), "sandbox": num(r, "sandbox"), "portal": reviews_usd,
                       "total": round(runs_usd + reviews_usd, 2), "active_days": active, "tasks_touched": int(r.get("tasks_touched") or 0),
                       "tasks_submitted": submitted, "requests": int(r.get("requests") or 0),
                       "per_task": round((runs_usd + reviews_usd) / submitted, 2) if submitted else None,
                       "per_active_day": round(runs_usd / active, 2) if active else None})
    people.sort(key=lambda p: -p["total"])

    add = lambda key: round(sum(d[key] for d in daily), 2)  # noqa: E731
    cb_days = sum(d["active_cbs"] for d in daily)
    task_totals = [t["total"] for t in tasks]
    recent = [d for d in daily if d["day"] > today - COVERAGE_DAYS * DAY]
    sub_runs, sub_logged = sum(d["sub_runs"] for d in recent), sum(d["sub_runs_logged"] for d in recent)
    return {
        "days": days, "start": start, "today": today,
        "summary": {
            "total": add("total"), "task_runs": add("task_runs"), "runs": add("runs"), "runs_linked": add("runs_linked"),
            "unlinked": round(add("runs") - add("runs_linked"), 2), "sandbox": add("sandbox"), "hint": add("hint"), "portal": add("portal"),
            "per_cb_day": round((add("task_runs") + add("portal")) / cb_days, 2) if cb_days else None,
            "tasks_submitted": len(tasks), "submitters": len({s for t in tasks for s in t["submitters"].split(", ") if s}),
            "per_task_median": round(statistics.median(task_totals), 2) if task_totals else None,
            "per_task_mean": round(statistics.fmean(task_totals), 2) if task_totals else None,
            "active_cbs": sum(1 for p in people if p["task_runs"] > 0),
            "portal_billed": round(portal_basis["billed"], 2), "portal_estimated": round(portal_basis["estimated"], 2),
        },
        # How many task runs in the last few days' submissions have any spend in LiteLLM's log or SMIG: low = both miss them.
        "coverage": {"days": COVERAGE_DAYS, "runs": sub_runs, "logged": sub_logged,
                     "pct": round(100 * sub_logged / sub_runs) if sub_runs else None},
        "daily": daily, "tasks": tasks, "people": people,
        "queries": {k: f"{config.REDASH_URL}/queries/{q}" for k, q in
                    (("daily", config.COST_DAY_QUERY_ID), ("tasks", config.COST_TASK_QUERY_ID), ("people", config.COST_PERSON_QUERY_ID))},
        "fetched_at": time.time(),
    }


async def report(days, force=False):
    """The cost report for the last `days` (7, 14 or 30), cached 10 minutes; keeps the last good one if Redash fails.
    Once cached figures are older than that, they're returned at once and refreshed in the background: the queries
    read LiteLLM and SMIG and take 30-45 s each, so the page shouldn't wait on them."""
    days = days if days in RANGES else 14
    hit = _cache.get(days)
    if hit and not force:
        if time.time() - hit[0] >= CACHE_SEC and days not in _refreshing:
            _refreshing[days] = asyncio.create_task(_refresh(days))
        return {**hit[1], "refreshing": True} if days in _refreshing else hit[1]
    return await _load(days, force, hit)


_refreshing = {}  # days -> running background refresh (kept so the task isn't garbage-collected)


async def _refresh(days):
    try:
        await _load(days, False, _cache.get(days))
    finally:
        _refreshing.pop(days, None)


async def _load(days, force, hit):
    params, age = {"days": days}, 0 if force else CACHE_SEC
    billed_lookup = (auth.redash_rows(config.COST_PORTAL_SMIG_QUERY_ID, max_age=age, timeout=LOOKUP_TIMEOUT)
                     if config.COST_PORTAL_SMIG_QUERY_ID else asyncio.sleep(0, result=[]))
    try:
        day_rows, task_rows, person_rows, billed_rows = await asyncio.gather(
            *(auth.redash_rows(q, params, max_age=age, timeout=LOOKUP_TIMEOUT)
              for q in (config.COST_DAY_QUERY_ID, config.COST_TASK_QUERY_ID, config.COST_PERSON_QUERY_ID)),
            billed_lookup, return_exceptions=True)
        for rows in (day_rows, task_rows, person_rows):
            if isinstance(rows, Exception):
                raise rows
    except Exception as error:  # noqa: BLE001 - the page shows the last figures instead of breaking
        log.warning("cost lookup failed: %s", error)
        if hit:
            return {**hit[1], "stale": str(error)[:200]}
        return {"days": days, "error": f"Couldn't read the cost data: {str(error)[:200]}"}
    if isinstance(billed_rows, Exception):  # optional: portal reviews fall back to the estimate
        log.warning("portal SMIG cost lookup failed: %s", billed_rows)
        billed_rows = []
    data = build(days, day_rows, task_rows, person_rows, billed_rows)
    _cache[days] = (time.time(), data)
    return data
