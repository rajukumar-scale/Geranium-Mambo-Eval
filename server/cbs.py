"""The admin CB tracker (/admin/cbs): how Atlas Commercial contributors do at L-1, kept live from Evan's P1.1 analysis
(2026-10-29, p1-1-cb-efficiency-analysis): benchmarks per group, learning curves, trusted conversion and who needs
training or offboarding.

A row is one task x contributor at L-1, first opened since START. Opened = the earliest of their claim, an incomplete
attempt, a submit minus its time spent, or their first portal chat for that task (a re-served node overwrites its
WORKER and earlier holders often have no incomplete row, so the portal's chats are holders too). Outcome = submitted
(an L-1 submit), working (a live claim) or dropped.

From the portal: hours = from their first portal activity on the task to when /injection, /prompt, /oe and /rubric had
all passed ("portal hours"); $ = their own runs' cost_estimate (the list-price estimate, as in the analysis, so the
bars stay comparable); reviews = their review runs; tries = runs until the first pass, per stage. Admins' runs and
other people's runs don't count.

Groups, on the day the task was first opened: QMA (worker source qm_auditor), trusted (mcp_ac_trusted that day) or
non-trusted. Internal accounts (queue_manager, Scale API, @scale.com) are left out.

The bars are fixed at the analysis's numbers (Evan, 2026-10-30: a target for training and offboarding shouldn't move);
the page also shows what the same cut (p50 / p75 / p90 of QMA + trusted submitted tasks) gives on recent data.

Statuses (Evan, 2026-10-30), checked in this order:
- Offboarded: carries the mcp_ac_temp_block tag right now (USERS.TAGS; Evan, 2026-11-01: that tag is how
  people are offboarded). Done, so listed last; removing the tag gives them their usual status back.
- Offboard: trained, and the last 2 claims closed since the training date both went badly.
- Needs training: not trained, past the first 2 claims (grace), and the last 2 closed claims ended with no submit, or
  the last 2 submitted tasks were both over the Flag bar (hours, $ or reviews).
- Trained: trained, fewer than 2 claims closed since.
- Grace: 2 claims or fewer.
- Efficient: at least one submitted task within the Efficient bar (hours and $).
- Acceptable: at least one submitted task within the Acceptable bar (hours and $), none within the Efficient bar
  (Evan, 2026-11-03: split out of Efficient, which until then meant the Acceptable bar).
- No flag: everyone else.
A claim went badly = dropped, or submitted over the Flag bar. Nobody goes straight to Offboard without being trained.

Only claims they worked on count (worked(): a review or a submit, as the analysis's "engaged" filter), plus live claims.
"""

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

from . import auth, config, db

log = logging.getLogger("portal.cbs")
START = 1791849600  # 2026-10-13 00:00 UTC, as in the analysis
REFRESH_SEC = 1800
RETRY_SEC = 120
LOOKUP_TIMEOUT = 180
RECENT_DAYS = 14  # the "bars from recent data" reference
GRACE = 2
STEPS = ("injection", "prompt", "hardness", "oe", "rubric")
REQUIRED = ("injection", "prompt", "oe", "rubric")
PASSING = ("PASS", "NON-FAIL")
GROUPS = ("QMA", "trusted", "non-trusted")
# p50 / p75 / p90 of QMA + trusted submitted tasks, 2026-10-13 to 10-29 (the P1.1 analysis)
BARS = {"efficient": {"hours": 28, "cost": 266, "reviews": 22},
        "acceptable": {"hours": 37, "cost": 342, "reviews": 32},
        "flag": {"hours": 55, "cost": 493, "reviews": 41}}
CACHE_FILE = config.DATA_DIR / "cbs_facts.json"
_facts = {"at": 0.0, "rows": None, "task": None, "error": None, "failed_at": 0.0}
_report = {"key": None, "data": None}


def _q(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    i = (len(values) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (i - lo)


def _r(value, digits=1):
    return None if value is None else round(value, digits)


def _day(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def _week(t):
    """The Monday (UTC) of t's week, as YYYY-MM-DD."""
    d = datetime.fromtimestamp(t, timezone.utc)
    return datetime.fromtimestamp(t - d.weekday() * 86400, timezone.utc).strftime("%Y-%m-%d")


def _portal():
    """The portal's chats and the contributors' own runs."""
    with db.reading():
        chats = db.q("select attempt_id, task_id, user_id, email, created_at, archived_from from chats "
                     "where task_id is not null and user_id is not null and created_at >= ?", (START - 7 * 86400,))
        runs = db.q("select attempt_id, kind, command, verdict, created_at, finished_at, cost_estimate, actor_user_id, "
                    "coalesce(actor_admin, 0) by_admin from runs where coalesce(loadtest, 0) = 0 and created_at >= ?",
                    (START - 7 * 86400,))
    return chats, runs


def build_rows(facts, chats, runs, now=None):
    """One row per task x contributor at L-1 since START (see the module docstring)."""
    now = now or time.time()
    nodes, submits, incompletes, users = defaultdict(list), defaultdict(list), defaultdict(list), {}
    l1_nodes = set()
    for f in facts:
        f = {k.lower(): v for k, v in f.items()}
        kind = f["kind"]
        if kind == "user":
            users[f["worker"]] = f
        elif kind == "node":
            l1_nodes.add(f["node"])
            nodes[(f["task"], f["worker"])].append(f)
        elif kind == "submit":
            submits[(f["task"], f["worker"])].append(f)
        elif kind == "incomplete":
            incompletes[(f["task"], f["worker"])].append(f)
    chat_key, first_chat, chat_email = {}, {}, {}
    for c in chats:
        if c["email"]:
            chat_email[c["user_id"]] = c["email"]
        key = (c["task_id"], c["user_id"])
        chat_key[c["attempt_id"]] = key
        if (c["archived_from"] or c["attempt_id"]) in l1_nodes:
            first_chat[key] = min(first_chat.get(key, c["created_at"]), c["created_at"])
    own = defaultdict(list)
    for r in runs:
        key = chat_key.get(r["attempt_id"])
        if not key or r["by_admin"] or (r["actor_user_id"] and r["actor_user_id"] != key[1]):
            continue
        own[key].append(r)

    def group(uid, day):
        u = users.get(uid) or {}
        email = (u.get("email") or "").lower()
        if u.get("source") == "qm_auditor":
            return "QMA"
        if u.get("source") in ("queue_manager", "Scale API") or "@scale.com" in email:
            return "internal"
        return "trusted" if u.get("tr_first") and u["tr_first"] <= day <= (u.get("tr_last") or "") else "non-trusted"

    # The tag snapshot is daily, so "tagged now" = tagged on the newest day any tag row reached.
    tag_day = max([u.get("tr_last") or "" for u in users.values()] + [u.get("blk_last") or "" for u in users.values()],
                  default="")
    offboarded = {uid: u["blk_first"] for uid, u in users.items() if tag_day and u.get("blk_last") == tag_day}
    # blk_now (2026-11-02): the live tag from USERS.TAGS. The daily snapshot lags a day, so someone tagged today showed
    # as "Needs training"; the live tag is the truth when the query has it (also when a tag was removed today).
    if any("blk_now" in u for u in users.values()):
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        offboarded = {uid: u.get("blk_first") or today for uid, u in users.items() if u.get("blk_now") in (True, 1, "true", "True")}

    rows = []
    for key in set(nodes) | set(submits) | set(incompletes) | set(first_chat):
        task, uid = key
        opens = [n["t1"] for n in nodes.get(key, []) if n["t1"]] + [i["t1"] for i in incompletes.get(key, []) if i["t1"]]
        opens += [s["t1"] - (s["secs"] or 0) for s in submits.get(key, []) if s["t1"]]
        if key in first_chat:
            opens.append(first_chat[key])
        if not opens or min(opens) < START:
            continue
        first_open = min(opens)
        subs = sorted(s["t1"] for s in submits.get(key, []) if s["t1"] and s["t1"] >= first_open - 3600)
        live = any(n["status"] == "pending" and n["t2"] and n["t2"] > now for n in nodes.get(key, []))
        outcome = "submitted" if subs else ("working" if live else "dropped")
        mine = sorted(own.get(key, []), key=lambda r: r["created_at"])
        began = ([first_chat[key]] if key in first_chat else []) + [r["created_at"] for r in mine[:1]]
        start = min(began) if began else None
        tries, passed, count = {}, {}, defaultdict(int)
        reviews = 0
        for r in mine:
            if r["kind"] != "review" or r["command"] not in STEPS:
                continue
            reviews += 1
            count[r["command"]] += 1
            if r["verdict"] in PASSING and r["command"] not in passed and r["finished_at"]:
                passed[r["command"]] = r["finished_at"]
                tries[r["command"]] = count[r["command"]]
        hours = (max(passed[s] for s in REQUIRED) - start) / 3600 if start and all(s in passed for s in REQUIRED) else None
        u = users.get(uid) or {}
        email = u.get("email") or chat_email.get(uid) or ""
        rows.append({
            "task": task, "user": uid, "email": re.sub(r"\+[^@]*@", "@", re.sub(r"(@[^+]+)\+.*$", r"\1", email)), "group": group(uid, _day(first_open)),
            "first_open": first_open, "outcome": outcome, "submitted_at": subs[0] if subs else None,
            "portal": bool(mine or key in first_chat), "hours": _r(hours, 2),
            "cost": round(sum(r["cost_estimate"] or 0 for r in mine), 2), "reviews": reviews, "tries": tries,
            "runs_per_stage": dict(count), "offboarded": offboarded.get(uid)})
    return rows


def over_flag(row):
    flag = BARS["flag"]
    return row["outcome"] == "submitted" and ((row["hours"] or 0) > flag["hours"] or row["cost"] > flag["cost"]
                                              or row["reviews"] > flag["reviews"])


def within_acceptable(row):
    acc = BARS["acceptable"]
    return row["outcome"] == "submitted" and row["hours"] is not None and row["hours"] <= acc["hours"] and row["cost"] <= acc["cost"]


def within_efficient(row):
    eff = BARS["efficient"]
    return row["outcome"] == "submitted" and row["hours"] is not None and row["hours"] <= eff["hours"] and row["cost"] <= eff["cost"]


def went_badly(row):
    return row["outcome"] == "dropped" or over_flag(row)


def status_of(claims, trained_at=None):
    """claims: one person's rows, oldest first. Returns (status, reason)."""
    if claims and claims[-1].get("offboarded"):
        return "offboarded", f"Tagged mcp_ac_temp_block since {claims[-1]['offboarded']}"
    closed = [r for r in claims if r["outcome"] != "working"]
    if trained_at:
        since = [r for r in closed if r["first_open"] >= trained_at]
        if len(since) >= 2 and went_badly(since[-1]) and went_badly(since[-2]):
            return "offboard", "Still lagging after training: the last 2 claims since then went badly"
        if len(since) < 2:
            return "trained", f"Trained · {len(since)} of 2 claims closed since"
    elif len(claims) > GRACE:
        if len(closed) >= 2 and closed[-1]["outcome"] == "dropped" and closed[-2]["outcome"] == "dropped":
            return "needs_training", "The last 2 closed claims ended with no submit"
        done = [r for r in closed if r["outcome"] == "submitted"]
        if len(done) >= 2 and over_flag(done[-1]) and over_flag(done[-2]):
            return "needs_training", "The last 2 submitted tasks were both over the Flag bar"
    if len(claims) <= GRACE and not trained_at:
        return "grace", f"Grace period: {len(claims)} of {GRACE} tasks"
    if any(within_acceptable(r) for r in claims):
        if any(within_efficient(r) for r in claims):
            return "efficient", "Submitted a task within the Efficient bar"
        return "acceptable", "Submitted a task within the Acceptable bar (none within the Efficient bar yet)"
    return "ok", ""


def performance(claims):
    """The four ranking factors (Evan, 2026-11-03), most important first:
    1. submit rate: submitted / claims (lower = worse);
    2. spend per submitted task: portal spend / submitted, the whole spend with no submit (higher = worse);
    3. hours per submitted task: portal hours / submitted, all the hours with no submit (higher = worse);
    4. recent: the last 2 closed claims both went badly (no submit, or over the Flag bar) = worse."""
    submitted = sum(r["outcome"] == "submitted" for r in claims)
    spend = sum(r["cost"] or 0 for r in claims)
    measured = [r["hours"] for r in claims if r["hours"] is not None]  # portal hours exist once all 4 stages passed
    hours = sum(measured)
    closed = [r for r in claims if r["outcome"] != "working"]
    return {"submit_rate": round(submitted / len(claims), 3) if claims else None,
            "spend_per_submit": round(spend / submitted if submitted else spend, 1),
            "hours_per_submit": round(hours / submitted if submitted else hours, 1) if measured else None,
            "recent_bad": len(closed) >= 2 and went_badly(closed[-1]) and went_badly(closed[-2])}


def rank_key(p):
    """Worst first inside a status group: each factor only breaks ties in the one before it."""
    return (p["submit_rate"] if p["submit_rate"] is not None else 1, -p["spend_per_submit"], -(p["hours_per_submit"] or 0),
            0 if p["recent_bad"] else 1, -p["last"])


def best_key(p):
    """Best first among people who submitted: highest submit rate, then lowest $ per submit, then fewest hours per
    submit (unmeasured last), then not 2 bad claims in a row."""
    hours = p["hours_per_submit"]
    return (-(p["submit_rate"] or 0), p["spend_per_submit"], hours if hours is not None else float("inf"),
            1 if p["recent_bad"] else 0, -p["last"])


def _training():
    return {r["user_id"]: r for r in db.q("select user_id, email, trained_at, trained_by, note from cb_training")}


def _stats(rows):
    done = [r for r in rows if r["outcome"] == "submitted" and r["portal"]]
    return {"cbs": len({r["user"] for r in rows}), "claims": len(rows), "submitted": sum(r["outcome"] == "submitted" for r in rows),
            "hours_p50": _r(_q([r["hours"] for r in rows], .5)), "hours_p90": _r(_q([r["hours"] for r in rows], .9)),
            "cost_p50": _r(_q([r["cost"] for r in done], .5), 0), "reviews_p50": _r(_q([r["reviews"] for r in done], .5)),
            "prompt_tries_p50": _r(_q([r["tries"].get("prompt") for r in done], .5)),
            "rubric_tries_p50": _r(_q([r["tries"].get("rubric") for r in done], .5)), "measured": len(done)}


def worked(row):
    """A claim they worked on: at least one review, or a submit. The analysis also counted 30+ active minutes, which
    the portal can't see; claims nobody worked on are left out of every figure and counted per person."""
    return row["reviews"] > 0 or row["outcome"] == "submitted"


def report(rows, training, now=None):
    now = now or time.time()
    untouched = defaultdict(int)
    for r in rows:
        if r["group"] != "internal" and not worked(r) and r["outcome"] != "working":
            untouched[r["user"]] += 1
    rows = [r for r in rows if r["group"] != "internal" and (worked(r) or r["outcome"] == "working")]
    by = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["first_open"]):
        by[r["user"]].append(r)
    for claims in by.values():
        for i, r in enumerate(claims, 1):
            r["n"] = i

    # 1. benchmarks, and what the bars would be from recent QMA + trusted submitted tasks
    groups = {g: _stats([r for r in rows if r["group"] == g]) for g in GROUPS}
    recent = [r for r in rows if r["group"] in ("QMA", "trusted") and r["outcome"] == "submitted" and r["portal"]
              and r["first_open"] >= now - RECENT_DAYS * 86400]
    recent_bars = {name: {m: _r(_q([r[m] for r in recent], p), 0 if m == "cost" else 1) for m in ("hours", "cost", "reviews")}
                   for name, p in (("efficient", .5), ("acceptable", .75), ("flag", .9))}
    weeks = []
    for week in sorted({_week(r["first_open"]) for r in rows}):
        w = [r for r in rows if _week(r["first_open"]) == week]
        weeks.append({"week": week, **{g: _stats([r for r in w if r["group"] == g]) for g in GROUPS}})

    # 2. learning curves: the Nth task a person opened (external contributors), and the same person's 1st vs 2nd
    external = [r for r in rows if r["group"] != "QMA"]
    curve = []
    for label, pick in (("1st", lambda r: r["n"] == 1), ("2nd", lambda r: r["n"] == 2), ("3rd", lambda r: r["n"] == 3),
                        ("4th+", lambda r: r["n"] >= 4)):
        curve.append({"task": label, **_stats([r for r in external if pick(r)])})
    pairs = []
    for claims in by.values():
        if claims[0]["group"] == "QMA":
            continue
        measured = [r for r in claims if r["hours"] is not None]
        if len(measured) >= 2:
            pairs.append((measured[0]["hours"], measured[1]["hours"]))
    paired = {"people": len(pairs), "first_p50": _r(_q([a for a, _ in pairs], .5)), "second_p50": _r(_q([b for _, b in pairs], .5)),
              "faster": sum(b < a for a, b in pairs)}

    # 3 + 4. each person's status, and trusted conversion (external contributors who claimed since START)
    people = []
    for uid, claims in by.items():
        grp = "QMA" if claims[0]["group"] == "QMA" else claims[-1]["group"]
        t = training.get(uid)
        status, reason = status_of(claims, t["trained_at"] if t else None)
        done = [r for r in claims if r["outcome"] == "submitted" and r["portal"]]
        people.append({**performance(claims),
            "user": uid, "email": claims[0]["email"], "group": grp, "status": status, "reason": reason,
            "claims": len(claims), "submitted": sum(r["outcome"] == "submitted" for r in claims),
            "working": sum(r["outcome"] == "working" for r in claims), "untouched": untouched.get(uid, 0),
            "hours_p50": _r(_q([r["hours"] for r in claims], .5)), "cost_p50": _r(_q([r["cost"] for r in done], .5), 0),
            "reviews_p50": _r(_q([r["reviews"] for r in done], .5)), "spend": round(sum(r["cost"] for r in claims)),
            "first": claims[0]["first_open"], "last": claims[-1]["first_open"],
            "trained": t and {"at": t["trained_at"], "by": t["trained_by"], "note": t["note"]},
            "tasks": [{k: r[k] for k in ("task", "first_open", "outcome", "hours", "cost", "reviews", "tries", "n")}
                      | {"over_flag": over_flag(r), "acceptable": within_acceptable(r), "efficient": within_efficient(r)}
                      for r in reversed(claims)]})
    ext_people = [p for p in people if p["group"] != "QMA"]
    eff = lambda p: any(t["acceptable"] for t in p["tasks"])
    cohorts = []
    for week in sorted({_week(p["first"]) for p in ext_people}):
        c = [p for p in ext_people if _week(p["first"]) == week]
        cohorts.append({"week": week, "claimed": len(c), "submitted": sum(p["submitted"] > 0 for p in c), "efficient": sum(map(eff, c))})
    qma = [p for p in people if p["group"] == "QMA"]
    conversion = {"claimed": len(ext_people), "submitted": sum(p["submitted"] > 0 for p in ext_people),
                  "measured": sum(1 for p in ext_people if any(t["hours"] is not None and t["outcome"] == "submitted" for t in p["tasks"])),
                  "efficient": sum(map(eff, ext_people)), "qma": len(qma), "qma_efficient": sum(map(eff, qma)), "cohorts": cohorts}
    statuses = defaultdict(lambda: {"QMA": 0, "trusted": 0, "non-trusted": 0, "spend": 0})
    for p in people:
        statuses[p["status"]][p["group"]] += 1
        statuses[p["status"]]["spend"] += p["spend"]
    order = {"offboard": 0, "needs_training": 1, "trained": 2, "grace": 3, "ok": 4, "acceptable": 5, "efficient": 6,
             "offboarded": 7}
    people.sort(key=lambda p: (order[p["status"]], *rank_key(p)))
    seen = defaultdict(int)
    for p in people:  # 1 = the worst in their status group
        seen[p["status"]] += 1
        p["rank"] = seen[p["status"]]
    # Everyone who has ever submitted, whatever their status, best first (Evan, 2026-11-03): the same four factors
    # the other way round. 1 = the best.
    submitters = sorted((p for p in people if p["submitted"] > 0), key=best_key)
    for i, p in enumerate(submitters, 1):
        p["best_rank"] = i
    return {"start": START, "bars": BARS, "recent_bars": recent_bars, "recent_days": RECENT_DAYS, "recent_n": len(recent),
            "groups": groups, "weeks": weeks, "curve": curve, "paired": paired, "conversion": conversion,
            "statuses": dict(statuses), "people": people}


async def get(force=False):
    """The tracker, from the cached Snowflake facts plus the portal's live runs (rebuilt at most once a minute)."""
    if force or not _facts["rows"]:
        kick(force=force)
    if not _facts["rows"]:
        return {"error": _facts["error"]} if _facts["error"] else {"loading": True}
    training = _training()
    key = (_facts["at"], json.dumps(training, sort_keys=True), int(time.time() // 60))
    if _report["key"] != key:
        chats, runs = await asyncio.to_thread(_portal)
        _report.update(key=key, data=report(build_rows(_facts["rows"], chats, runs), training))
    data = dict(_report["data"], fetched_at=_facts["at"], refreshing=bool(_facts["task"]))
    if _facts["error"]:
        data["stale"] = _facts["error"]
    return data


def mark_trained(user_id, email, by, note=""):
    db.ex("insert or replace into cb_training(user_id, email, trained_at, trained_by, note) values (?,?,?,?,?)",
          (user_id, email, time.time(), by, (note or "")[:300]))
    _report["key"] = None


def unmark_trained(user_id):
    db.ex("delete from cb_training where user_id=?", (user_id,))
    _report["key"] = None


def kick(force=False):
    if _facts["task"] or (not force and time.time() - _facts["failed_at"] < RETRY_SEC):
        return
    _facts["task"] = asyncio.create_task(_refresh())


async def _refresh():
    try:
        # The portal's own contributors too: a re-served node overwrites its WORKER, so some holders are only in chats.
        ids = {r["user_id"] for r in db.q("select distinct user_id from chats where created_at >= ?", (START - 7 * 86400,))}
        extra = ",".join(sorted(i for i in ids if i and re.fullmatch(r"[0-9a-f]{24}", i))) or "x"
        rows = await auth.redash_rows(config.CB_FACTS_QUERY_ID, {"extra_users": extra}, timeout=LOOKUP_TIMEOUT)
        _facts.update(at=time.time(), rows=rows, error=None, failed_at=0.0)
        _save()
    except Exception as error:  # noqa: BLE001 - the page keeps the last figures and says so
        log.warning("CB tracker lookup failed: %s", error)
        _facts.update(error=f"Couldn't read the claims data: {str(error)[:200]}", failed_at=time.time())
    finally:
        _facts["task"] = None


def _save():
    try:
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": _facts["at"], "rows": _facts["rows"]}))
        tmp.replace(CACHE_FILE)
    except OSError as error:
        log.warning("couldn't save the CB tracker cache: %s", error)


def restore():
    try:
        saved = json.loads(CACHE_FILE.read_text())
        _facts.update(at=float(saved["at"]), rows=saved["rows"])
    except (OSError, ValueError, KeyError, TypeError):
        pass


async def loop():
    restore()
    while True:
        if config.CB_FACTS_QUERY_ID and time.time() - _facts["at"] >= REFRESH_SEC:
            kick()
        await asyncio.sleep(60)
