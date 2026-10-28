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

The queries take about a minute, longer than a proxy waits, so one query returns every fact since 2026-08-01 (per day,
per task x contributor x day, and each submission). It's refreshed in the background every 15 minutes and kept on disk
across restarts; any date range is then cut from it at once.
"""

import asyncio
import calendar
import json
import logging
import statistics
import time

from . import auth, config, db, spend

log = logging.getLogger("portal.cost")
DAY = 86400
START = 1785542400  # 2026-08-01 00:00 UTC: the facts query starts here (task runs on the project's key start 08-10)
REFRESH_SEC = 900
RETRY_SEC = 120  # after a failed refresh, wait this long before trying again
LOOKUP_TIMEOUT = 300  # the query scans LiteLLM + SMIG; Redash can also queue it behind other work
COVERAGE_DAYS = 5  # how far back from the range's end the "task runs not logged" check looks
CACHE_FILE = config.DATA_DIR / "cost_facts.json"
_facts = {"at": 0.0, "rows": None, "billed": [], "task": None, "error": None, "failed_at": 0.0}


def _rows(rows):
    return [{k.lower(): v for k, v in row.items()} for row in rows]


def _portal(days_start, billed=None, days_end=None):
    """This portal's run costs: per UTC day in the range (and who ran them), per task (up to the range's end) and per
    contributor (in the range). A run SMIG billed (tagged with its runId) counts at that cost; older runs at the list-price
    estimate, which runs ~40% above what's billed. Also returns how much of the range is billed vs estimated."""
    stop = (days_end + DAY) if days_end is not None else float("inf")
    billed = billed or {}
    per_day, per_task, per_person, people_day = {}, {}, {}, {}
    basis = {"billed": 0.0, "estimated": 0.0}
    for run in db.q("select r.id, r.cost_estimate, r.models, r.created_at, coalesce(r.actor_admin, 0) by_admin, r.actor_user_id, c.task_id "
                    "from runs r left join chats c on c.attempt_id = r.attempt_id where coalesce(r.loadtest, 0) = 0"):
        usd = billed[run["id"]] if run["id"] in billed else spend.run_spend(run)
        if not usd or run["created_at"] >= stop:
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


def build(facts, start, end, billed_rows=(), days=None):
    """The report for UTC days start..end (epoch seconds of each day's midnight), cut from the facts query's rows."""
    today = int(time.time() // DAY * DAY)
    billed = {r["run_id"]: float(r.get("cost_usd") or 0) for r in _rows(billed_rows) if r.get("run_id")}
    portal_day, portal_task, portal_person, portal_people, portal_basis = _portal(start, billed, end)
    rows = _rows(facts)
    num = lambda r, k: float(r.get(k) or 0)  # noqa: E731
    stop = end + DAY
    logged = {int(r["t"]): r for r in rows if r["kind"] == "day"}
    tcd = [r for r in rows if r["kind"] == "tcd"]
    subs = [r for r in rows if r["kind"] == "sub" and start <= int(r["t"]) < stop]
    sub_day = {}
    for r in subs:
        d = sub_day.setdefault(int(r["t"]) // DAY * DAY, ({}, set()))
        d[0][r["task"]] = 1
        d[1].add(r["cb"])

    daily = []
    for d in range(start, stop, DAY):
        r = logged.get(d, {})
        runs, linked, sandbox, hint, portal = num(r, "runs"), num(r, "runs_linked"), num(r, "sandbox"), num(r, "hint"), portal_day.get(d, 0)
        people = {c for c in (r.get("cb_ids") or "").split(",") if c} | portal_people.get(d, set())
        day_tasks, day_submitters = sub_day.get(d, ({}, set()))
        daily.append({"day": d, "today": d == today, "runs": runs, "runs_linked": linked, "sandbox": sandbox, "hint": hint,
                      "portal": round(portal, 2), "task_runs": round(runs + sandbox, 2), "total": round(runs + sandbox + hint + portal, 2),
                      "requests": int(r.get("requests") or 0), "active_cbs": len(people), "people": people,
                      "tasks_touched": int(r.get("tasks_touched") or 0), "tasks_submitted": len(day_tasks),
                      "submitters": len(day_submitters), "sub_runs": int(r.get("sub_runs") or 0), "sub_runs_logged": int(r.get("sub_runs_logged") or 0)})

    # tasks submitted in the range; their cost is everything tied to them up to the range's end
    by_task = {}
    for r in subs:
        t = by_task.setdefault(r["task"], {"last": 0, "n": 0, "who": set(), "ids": set()})
        t["last"], t["n"] = max(t["last"], int(r["t"])), t["n"] + 1
        t["who"].add(r.get("email") or "unknown")
        t["ids"].add(r["cb"])
    task_spend = {}
    for r in tcd:
        if r.get("task") in by_task and int(r["t"]) < stop:
            p = task_spend.setdefault(r["task"], {"runs": 0.0, "sandbox": 0.0, "requests": 0, "cbs": set(), "first": None})
            p["runs"] += num(r, "runs")
            p["sandbox"] += num(r, "sandbox")
            p["requests"] += int(r.get("requests") or 0)
            if r.get("cb"):
                p["cbs"].add(r["cb"])
            p["first"] = min(p["first"] or int(r["t"]), int(r["t"]))
    tasks = []
    for task_id, t in by_task.items():
        p = task_spend.get(task_id, {"runs": 0.0, "sandbox": 0.0, "requests": 0, "cbs": set(), "first": None})
        runs_usd, reviews_usd = round(p["runs"] + p["sandbox"], 2), round(portal_task.get(task_id, 0), 2)
        tasks.append({"task_id": task_id, "last_submitted": t["last"], "submissions": t["n"], "submitters": ", ".join(sorted(t["who"])),
                      "task_runs": runs_usd, "agent_runs": round(p["runs"], 2), "sandbox": round(p["sandbox"], 2), "portal": reviews_usd,
                      "total": round(runs_usd + reviews_usd, 2), "requests": p["requests"], "contributors": len(p["cbs"]), "first_run": p["first"]})
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
    people = []
    for cb, p in by_person.items():
        if "@scale.com" in p["email"]:
            continue
        runs_usd, reviews_usd = round(p["runs"] + p["sandbox"], 2), round(portal_person.get(cb, 0), 2)
        submitted, active = len(p["submitted"]), len(p["days"])
        people.append({"user_id": cb, "email": p["email"], "task_runs": runs_usd, "agent_runs": round(p["runs"], 2), "sandbox": round(p["sandbox"], 2),
                       "portal": reviews_usd, "total": round(runs_usd + reviews_usd, 2), "active_days": active, "tasks_touched": len(p["tasks"]),
                       "tasks_submitted": submitted, "requests": p["requests"],
                       "per_task": round((runs_usd + reviews_usd) / submitted, 2) if submitted else None,
                       "per_active_day": round(runs_usd / active, 2) if active else None})
    people.sort(key=lambda p: -p["total"])

    # the same days bucketed into UTC weeks (Monday start) and calendar months, clipped to the range
    periods = {}
    for period in ("week", "month"):
        out = {}
        for d in daily:
            b0 = _period_start(d["day"], period)
            b = out.setdefault(b0, {"day": b0, "runs": 0.0, "runs_linked": 0.0, "sandbox": 0.0, "hint": 0.0, "portal": 0.0, "people": set(),
                                    "tasks": set(), "cb_days": 0, "partial_start": b0 < start,
                                    "partial_end": _period_end(b0, period) > stop, "today": False})
            for k in ("runs", "runs_linked", "sandbox", "hint", "portal"):
                b[k] += d[k]
            b["people"] |= d["people"]
            b["cb_days"] += len(d["people"])
            b["today"] = b["today"] or d["today"]
        for r in subs:
            out[_period_start(int(r["t"]) // DAY * DAY, period)]["tasks"].add(r["task"])
        periods[period] = [{**{k: v for k, v in b.items() if k not in ("people", "tasks")},
                            **{k: round(b[k], 2) for k in ("runs", "runs_linked", "sandbox", "hint", "portal")},
                            "total": round(b["runs"] + b["sandbox"] + b["hint"] + b["portal"], 2),
                            "active_cbs": len(b["people"]), "tasks_submitted": len(b["tasks"])} for _, b in sorted(out.items())]
    for d in daily:
        d.pop("people")

    add = lambda key: round(sum(d[key] for d in daily), 2)  # noqa: E731
    cb_days = sum(d["active_cbs"] for d in daily)
    task_totals = [t["total"] for t in tasks]
    recent = [d for d in daily if d["day"] > end - COVERAGE_DAYS * DAY]
    sub_runs, sub_logged = sum(d["sub_runs"] for d in recent), sum(d["sub_runs_logged"] for d in recent)
    return {
        "days": days, "start": start, "end": end, "today": today,
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
        # How many task runs in the range's last few days' submissions have any spend in LiteLLM's log or SMIG: low = both miss them.
        "coverage": {"days": COVERAGE_DAYS, "runs": sub_runs, "logged": sub_logged,
                     "pct": round(100 * sub_logged / sub_runs) if sub_runs else None},
        "daily": daily, "weeks": periods["week"], "months": periods["month"], "tasks": tasks, "people": people,
        "queries": {"facts": f"{config.REDASH_URL}/queries/{config.COST_FACTS_QUERY_ID}"},
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
    data = build(_facts["rows"], lo, hi, _facts["billed"], days=None if start and end else int(days or 14))
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
        rows, billed = await asyncio.gather(auth.redash_rows(config.COST_FACTS_QUERY_ID, timeout=LOOKUP_TIMEOUT),
                                            billed_lookup, return_exceptions=True)
        if isinstance(rows, Exception):
            raise rows
        if isinstance(billed, Exception):  # optional: portal reviews fall back to the estimate
            log.warning("portal SMIG cost lookup failed: %s", billed)
            billed = _facts["billed"]
        _facts.update(at=time.time(), rows=rows, billed=billed, error=None, failed_at=0.0)
        _save()
    except Exception as error:  # noqa: BLE001 - the page keeps the last figures and says so
        log.warning("cost lookup failed: %s", error)
        _facts.update(error=f"Couldn't read the cost data: {str(error)[:200]}", failed_at=time.time())
    finally:
        _facts["task"] = None


def _save():
    try:
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": _facts["at"], "rows": _facts["rows"], "billed": _facts["billed"]}))
        tmp.replace(CACHE_FILE)
    except OSError as error:
        log.warning("couldn't save the cost cache: %s", error)


def restore():
    """Load the last saved facts, so a restart serves figures at once (refreshed in the background if old)."""
    try:
        saved = json.loads(CACHE_FILE.read_text())
        _facts.update(at=float(saved["at"]), rows=saved["rows"], billed=saved.get("billed") or [])
    except (OSError, ValueError, KeyError, TypeError):
        pass


async def loop():
    """Keep the facts fresh: restore the saved copy, then refresh whenever it's older than REFRESH_SEC."""
    restore()
    while True:
        if config.COST_FACTS_QUERY_ID and time.time() - _facts["at"] >= REFRESH_SEC:
            kick()
        await asyncio.sleep(60)
