"""The public status page (/status), laid out like status.claude.com: an overall banner, four components with a
90-day uptime bar each, and past incidents.

Incidents come from two places:
- automatic: computed on every refresh from the portal's own records, so they open and resolve themselves:
  - Eval Portal: the server answers a heartbeat every minute; 2+ missing minutes in a row = unavailable;
  - Review machines: a review that took longer than STATUS_SLOW_START_MIN to start (yellow), or reviews that
    could not get a machine (red when more than STATUS_RED_PCT of starts in the episode failed);
  - Reviews: finished reviews that crashed, per hour (yellow over STATUS_YELLOW_PCT, red over STATUS_RED_PCT);
  - Eval assistant: model calls that failed with a server error, per hour (same thresholds);
- manual: posted by an admin on the admin page, with Investigating / Identified / Monitoring / Resolved updates.

Nothing here names a contributor, an email or a task. Levels: 0 operational, 1 degraded, 2 partial, 3 major.
"""

import asyncio
import datetime as dt
import json
import logging
import re
import time

from . import config, db, hostload, notify

log = logging.getLogger("portal.status")

COMPONENTS = [
    ("portal", "Eval Portal", "The website and sign-in"),
    ("machines", "Review machines", "Starting a review's machine and uploading its files"),
    ("reviews", "Reviews", "/injection, /prompt, /hardness, /oe and /rubric runs"),
    ("assistant", "Eval assistant", "The model connection behind reviews and chat replies"),
]
NAMES = {k: n for k, n, _ in COMPONENTS}
LEVEL_WORDS = {0: "Operational", 1: "Degraded Performance", 2: "Partial Outage", 3: "Major Outage"}
IMPACTS = {"degraded": 1, "partial": 2, "major": 3}
MANUAL_STATUSES = ("Investigating", "Identified", "Monitoring", "Update", "Resolved")
MACHINE_ERROR = re.compile(r"boat new|review machine setup failed|upload to bx_|ready box", re.I)
DAYS, PAST_DAYS = 90, 15
MERGE_GAP = 15 * 60  # bad moments closer than this are one incident

_cache = {"at": 0.0, "data": None}
_lock = asyncio.Lock()


def _settings():
    return (getattr(config, "STATUS_SLOW_START_MIN", 10), getattr(config, "STATUS_YELLOW_PCT", 10),
            getattr(config, "STATUS_RED_PCT", 30))


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


def _hm(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%H:%M")


# ---- heartbeat ---------------------------------------------------------------

def beat():
    db.ex("insert or ignore into status_samples(minute) values (?)", (int(time.time() // 60),))


async def loop():
    """Heartbeat every 30 s; refresh the page data and send Slack notices for new or resolved incidents."""
    while True:
        try:
            beat()
            data = await current(force=True)
            await _notify(data)
        except Exception as error:  # noqa: BLE001 - the status loop must never stop
            log.warning("status refresh failed: %s", error)
        await asyncio.sleep(30)


# ---- automatic incidents -----------------------------------------------------

def _merge(events):
    """events: (start, end, info) -> merged episodes [(start, end, [info...])], sorted."""
    out = []
    for start, end, info in sorted(events, key=lambda e: e[0]):
        if out and start <= out[-1][1] + MERGE_GAP:
            out[-1][1] = max(out[-1][1], end)
            out[-1][2].append(info)
        else:
            out.append([start, end, [info]])
    return out


def _portal(now):
    minutes = [r["minute"] for r in db.q("select minute from status_samples order by minute")]
    if not minutes:
        return [], None
    have, first, last_needed = set(minutes), minutes[0], int(now // 60) - 1
    out, gap_start = [], None
    for m in range(first, last_needed + 1):
        if m not in have:
            gap_start = m if gap_start is None else gap_start
        elif gap_start is not None:
            if m - gap_start >= 2:
                out.append(dict(component="portal", level=3, start=gap_start * 60, end=m * 60,
                                title="Eval Portal unavailable",
                                detail=f"The portal didn't respond for {m - gap_start} minutes."))
            gap_start = None
    if gap_start is not None and last_needed + 1 - gap_start >= 2:
        out.append(dict(component="portal", level=3, start=gap_start * 60, end=None, title="Eval Portal unavailable",
                        detail="The portal isn't responding."))
    return out, first * 60


def _review_runs(since):
    return db.q("select created_at, finished_at, status, error, milestones, review_started_at from runs "
                "where kind='review' and coalesce(loadtest,0)=0 and coalesce(continuation,0)=0 and created_at>=? "
                "order by created_at", (since,))


def _started(run):
    if run["review_started_at"]:
        return run["review_started_at"]
    marks = json.loads(run["milestones"] or "{}")
    return min(marks.values()) if marks else None


def _machines(runs, now):
    slow_min, _, red = _settings()
    events = []
    for r in runs:
        if r["status"] == "cancelled":
            continue
        began = _started(r)
        if r["status"] == "failed" and MACHINE_ERROR.search(r["error"] or ""):
            events.append((r["created_at"], r["finished_at"] or r["created_at"], ("fail", 0)))
        elif began and began - r["created_at"] > slow_min * 60:
            events.append((r["created_at"], began, ("slow", (began - r["created_at"]) / 60)))
        elif not began and r["status"] in ("queued", "running") and now - r["created_at"] > slow_min * 60:
            events.append((r["created_at"], now, ("waiting", (now - r["created_at"]) / 60)))
    out = []
    for start, end, infos in _merge(events):
        starts = sum(1 for r in runs if start - 60 <= r["created_at"] <= end and r["status"] != "cancelled") or 1
        fails = sum(1 for kind, _ in infos if kind == "fail")
        slow = [m for kind, m in infos if kind != "fail"]
        ongoing = any(kind == "waiting" for kind, _ in infos)
        if fails >= 2 and fails * 100 > red * starts:  # one refused start is a blip, not an outage
            level, title = 3, "Review machines failing to start"
        else:
            level, title = 1, ("Reviews starting slowly" if slow else "Some review machines failed to start")
        parts = []
        if slow:
            parts.append(f"{len(slow)} review{'s' if len(slow) != 1 else ''} took over {slow_min:g} minutes to start "
                         f"(longest {max(slow):.0f} min).")
        if fails:
            parts.append(f"{fails} review{'s' if fails != 1 else ''} couldn't get a machine.")
        out.append(dict(component="machines", level=level, start=start, end=None if ongoing else end,
                        title=title, detail=" ".join(parts)))
    return out


def _hourly(component, rows, now, minimum, noun, title):
    """rows: (hour, total, failed) -> merged hourly incidents over the thresholds."""
    _, yellow, red = _settings()
    events = []
    for hour, total, failed in rows:
        if total < minimum or not failed:
            continue
        pct = failed * 100 / total
        if pct > yellow:
            events.append((hour * 3600, (hour + 1) * 3600, (3 if pct > red else 1, failed, total)))
    out = []
    for start, end, infos in _merge(events):
        failed, total = sum(i[1] for i in infos), sum(i[2] for i in infos)
        out.append(dict(component=component, level=max(i[0] for i in infos), start=start,
                        end=None if end > now else end, title=title,
                        detail=f"{failed} of {total} {noun} failed ({failed * 100 / total:.0f}%)."))
    return out


def _reviews(runs, now):
    hours = {}
    for r in runs:
        if r["status"] not in ("done", "failed") or not r["finished_at"]:
            continue
        if r["status"] == "failed" and MACHINE_ERROR.search(r["error"] or ""):
            continue  # counted under Review machines
        h = hours.setdefault(int(r["finished_at"] // 3600), [0, 0])
        h[0] += 1
        h[1] += r["status"] == "failed"
    return _hourly("reviews", [(h, t, f) for h, (t, f) in sorted(hours.items())], now, 3, "reviews",
                   "Elevated review failures")


def _assistant(since, now):
    rows = db.q("select cast(created_at / 3600 as integer) h, count(*) n, sum(coalesce(http_status, 0) >= 500) f "
                "from llm_calls where created_at >= ? group by h order by h", (since,))
    first = db.one("select min(created_at) t from llm_calls")
    return (_hourly("assistant", [(r["h"], r["n"], r["f"] or 0) for r in rows], now, 20, "model calls",
                    "Elevated eval assistant errors"), first["t"] if first else None)


# ---- manual incidents --------------------------------------------------------

def manual_incidents(since=0):
    out = []
    for i in db.q("select * from status_incidents where coalesce(resolved_at, 9e12) >= ? order by created_at desc", (since,)):
        updates = db.q("select status, body, created_at from status_updates where incident_id=? order by created_at desc",
                       (i["id"],))
        out.append(dict(id=i["id"], title=i["title"], components=json.loads(i["components"] or "[]"),
                        level=IMPACTS.get(i["impact"], 1), impact=i["impact"], start=i["created_at"],
                        end=i["resolved_at"], updates=updates, manual=True))
    return out


def _auto_updates(inc):
    ups = [dict(status="Investigating", body=f"Detected automatically. {inc['detail']}", created_at=inc["start"])]
    if inc["end"]:
        ups.insert(0, dict(status="Resolved", body="This has recovered.", created_at=inc["end"]))
    return ups


# ---- page data ---------------------------------------------------------------

def build(now=None):
    now = now or time.time()
    since = now - DAYS * 86400
    runs = _review_runs(since)
    portal, portal_from = _portal(now)
    assistant, assistant_from = _assistant(since, now)
    first_run = db.one("select min(created_at) t from runs where kind='review'")
    runs_from = first_run["t"] if first_run else None
    auto = portal + _machines(runs, now) + _reviews(runs, now) + assistant
    for inc in auto:
        inc["components"] = [inc.pop("component")]
        inc["key"] = f"{inc['components'][0]}:{int(inc['start'])}"
        inc["updates"] = _auto_updates(inc)
    incidents = sorted(auto + manual_incidents(since), key=lambda i: i["start"], reverse=True)
    data_from = {"portal": portal_from, "machines": runs_from, "reviews": runs_from, "assistant": assistant_from}

    today = _day(now)
    days = [today - dt.timedelta(days=n) for n in range(DAYS - 1, -1, -1)]
    components = []
    for key, name, desc in COMPONENTS:
        mine = [i for i in incidents if key in i["components"]]
        start_from = data_from[key]
        bars, down_seconds, covered = [], 0.0, 0.0
        for d in days:
            d0 = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()
            d1 = min(d0 + 86400, now)
            if not start_from or d1 <= start_from:
                bars.append(dict(date=d.isoformat(), level=None, notes=[]))
                continue
            covered += d1 - max(d0, start_from)
            level, notes = 0, []
            for i in mine:
                s, e = i["start"], i["end"] or now
                if s < d1 and e > d0:
                    level = max(level, i["level"])
                    notes.append(dict(title=i["title"], level=i["level"],
                                      span=f"{_hm(max(s, d0))}–{_hm(min(e, d1)) if i['end'] or e < d1 else 'now'} UTC"))
                    if i["level"] >= 3:
                        down_seconds += min(e, d1) - max(s, d0)
                    elif i["level"] == 2:
                        down_seconds += 0.3 * (min(e, d1) - max(s, d0))
            bars.append(dict(date=d.isoformat(), level=level, notes=notes))
        live = max([i["level"] for i in mine if not i["end"]] or [0])
        components.append(dict(key=key, name=name, description=desc, level=live, status=LEVEL_WORDS[live],
                               uptime=round(100 * (1 - down_seconds / covered), 2) if covered else None, bars=bars))
    worst = [c["level"] for c in components]
    reds = sum(1 for lvl in worst if lvl >= 3)
    overall = ("All Systems Operational" if max(worst) == 0 else "Degraded Performance" if max(worst) == 1
               else "Major System Outage" if reds >= 2 else "Partial System Outage")
    public = [dict(title=i["title"], components=[NAMES.get(c, c) for c in i["components"]], level=i["level"],
                   start=i["start"], end=i["end"], updates=i["updates"], manual=bool(i.get("manual")),
                   key=i.get("key") or f"m{i['id']}") for i in incidents]
    return dict(generated_at=now, overall=overall, overall_level=max(worst), components=components,
                incidents=public, thresholds=dict(zip(("slow_start_min", "yellow_pct", "red_pct"), _settings())),
                server=_server(now))


def _server(now):
    """The portal's own machine: memory, CPU, disk, and where reviews ran (this server or a boat box). Counts only."""
    day = now - 86400
    count = lambda sql, *a: db.one(sql, a)["n"]  # noqa: E731
    return dict(**hostload.snapshot(),
                review_slots=config.SERVER_REVIEW_SLOTS if config.REVIEW_RUNNER == "boat" else 0,
                reviews_here_now=count("select count(*) n from runs where status='running' and boat_phase='server'"),
                followups_now=count("select count(*) n from runs where status='running' and kind='followup'"),
                reviews_here_24h=count("select count(*) n from runs where kind='review' and boat_phase='server' "
                                       "and created_at>=? and coalesce(loadtest,0)=0", day),
                reviews_machines_24h=count("select count(*) n from runs where kind='review' and runner_box is not null "
                                           "and created_at>=? and coalesce(loadtest,0)=0", day))


async def current(force=False):
    async with _lock:
        if force or not _cache["data"] or time.time() - _cache["at"] > 60:
            _cache["data"] = await asyncio.to_thread(build)
            _cache["at"] = time.time()
        return _cache["data"]


def invalidate():
    _cache["at"] = 0.0


async def _notify(data):
    """Slack the admin channel when an automatic incident opens (if it began in the last 30 minutes) or resolves."""
    if not notify.configured():
        return
    now = time.time()
    base = (getattr(config, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    for inc in data["incidents"]:
        if inc["manual"]:
            continue
        key = inc["key"]
        if not inc["end"] and now - inc["start"] < 1800 and not db.one("select 1 from status_notified where key=?", ("open:" + key,)):
            db.ex("insert or ignore into status_notified(key, created_at) values (?,?)", ("open:" + key, now))
            await notify.slack(f"{'🔴' if inc['level'] >= 3 else '🟡'} Status: {inc['title']} since {_hm(inc['start'])} UTC "
                               f"({', '.join(inc['components'])}). {inc['updates'][-1]['body']} {base}/status")
        elif inc["end"] and db.one("select 1 from status_notified where key=?", ("open:" + key,)) \
                and not db.one("select 1 from status_notified where key=?", ("resolved:" + key,)):
            db.ex("insert or ignore into status_notified(key, created_at) values (?,?)", ("resolved:" + key, now))
            await notify.slack(f"✅ Resolved: {inc['title']} ({_hm(inc['start'])}–{_hm(inc['end'])} UTC). {base}/status")


def rss(data, base):
    from xml.sax.saxutils import escape
    items = []
    for i in data["incidents"][:50]:
        latest = i["updates"][0] if i["updates"] else {}
        when = dt.datetime.fromtimestamp(latest.get("created_at") or i["start"], dt.timezone.utc)
        items.append(f"<item><title>{escape(i['title'])}</title><link>{escape(base)}/status#{escape(i['key'])}</link>"
                     f"<guid isPermaLink=\"false\">{escape(i['key'])}</guid>"
                     f"<pubDate>{when.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
                     f"<description>{escape(latest.get('status', '') + ': ' + latest.get('body', ''))}</description></item>")
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel>"
            f"<title>Original Conference Eval Portal Status</title><link>{escape(base)}/status</link>"
            "<description>Incidents for the Original Conference Eval Portal</description>" + "".join(items) + "</channel></rss>")
