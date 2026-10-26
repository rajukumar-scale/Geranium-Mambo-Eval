"""The LiteLLM key's budget, for the admin page: this cycle's spend against the cap, the burn rate,
and when the key runs out at that pace.

The key is shared with the platform's own task traffic, and LiteLLM hard-stops it at the cap (it did
on 2026-10-11), so every review here fails until the cycle resets or the cap is raised. The numbers
come from LiteLLM's own tables plus SMIG's usage events on the key (most calls go through SMIG since ~2026-10-16 and never
reach LiteLLM), synced to Snowflake, through a saved Redash query.
"""

import asyncio
import logging
import re
import time

from . import auth, config, db, spend

log = logging.getLogger("portal.budget")
CACHE_SEC = 300
_cache = {"at": 0.0, "data": None}


def _runs_out(left, per_hour, now, reset):
    if not per_hour or left is None:
        return None
    at = now + left / per_hour * 3600
    return {"per_hour": round(per_hour, 2), "hours": round(left / per_hour, 1), "at": at,
            "before_reset": bool(reset) and at < reset}


def summarize(row):
    r = {k.lower(): v for k, v in row.items()}
    cap, spent = float(r.get("max_budget") or 0), float(r.get("spend") or 0)
    now, reset = float(r.get("checked_at") or time.time()), r.get("budget_reset_at")
    left = max(0.0, cap - spent) if cap else None
    pct = round(100 * spent / cap, 1) if cap else None
    days = int((re.match(r"(\d+)d", r.get("budget_duration") or "") or [0, 0])[1] or 0)
    cycle_start = reset - days * 86400 if reset and days else None
    pace_24h = _runs_out(left, (r.get("spend_24h") or 0) / 24, now, reset)
    pace_3h = _runs_out(left, (r.get("spend_3h") or 0) / 3, now, reset)
    # The portal's own share, from its runs' costs (LiteLLM sends no per-call cost header to the proxy).
    day_ago = time.time() - 86400
    runs = db.q("select cost_estimate, models, created_at from runs where created_at>=? and coalesce(loadtest,0)=0",
                (min(cycle_start or day_ago, day_ago),))
    portal = {"cycle": sum(spend.run_spend(x) for x in runs if x["created_at"] >= (cycle_start or day_ago)),
              "day": sum(spend.run_spend(x) for x in runs if x["created_at"] >= day_ago)}
    # Only LiteLLM's own counter can trip its block; SMIG spend counts toward the shown total, not the block.
    litellm_spent = float(r["litellm_spend"]) if r.get("litellm_spend") is not None else spent
    blocked = bool(r.get("blocked")) or (cap and litellm_spent >= cap)
    level = ("blocked" if blocked else "danger" if (pace_24h or {}).get("before_reset") or (pct or 0) >= 90
             else "warn" if (pct or 0) >= 75 else "ok")
    return {"key_alias": r.get("key_alias"), "max_budget": cap, "spend": spent, "left": left, "pct": pct,
            "litellm_spend": r.get("litellm_spend"), "smig_spend": r.get("smig_spend"),
            "cycle": r.get("budget_duration"), "cycle_start": cycle_start, "resets_at": reset,
            "spend_1h": r.get("spend_1h"), "spend_3h": r.get("spend_3h"), "spend_24h": r.get("spend_24h"),
            "pace_24h": pace_24h, "pace_3h": pace_3h, "blocked": blocked, "level": level,
            "portal_cycle": round(portal["cycle"], 2), "portal_24h": round(portal["day"], 2),
            "synced_at": r.get("key_synced_at"), "last_request_at": r.get("last_request_at"), "checked_at": now,
            "query_url": f"{config.REDASH_URL}/queries/{config.BUDGET_QUERY_ID}"}


DAY = 86400


def daily(rows, summary):
    """The last 14 UTC days: the key's spend and requests (LiteLLM's log + SMIG) and this portal's own estimate."""
    today = int(time.time() // DAY * DAY)
    days = [today - i * DAY for i in range(13, -1, -1)]
    logged = {int(r.get("day") or 0): r for r in ({k.lower(): v for k, v in row.items()} for row in rows)}
    portal = {d: 0.0 for d in days}
    for run in db.q("select cost_estimate, models, created_at from runs where created_at>=? and coalesce(loadtest,0)=0", (days[0],)):
        d = int(run["created_at"] // DAY * DAY)
        if d in portal:
            portal[d] += spend.run_spend(run)
    return [{"day": d, "spend": float((logged.get(d) or {}).get("spend") or 0), "requests": int((logged.get(d) or {}).get("requests") or 0),
             "portal": round(portal[d], 2), "today": d == today} for d in days]


def log_gap(summary):
    """Neither LiteLLM nor SMIG has recorded this key's requests while the portal kept calling it (LiteLLM alone, 2026-10-15): then the
    key's spend, and the bars, under-count. Returns when the log went quiet and how many calls the portal made since."""
    last = summary.get("last_request_at")
    if not last:
        return None
    calls = db.one("select count(*) n from llm_calls where http_status=200 and created_at>?", (last + 600,))["n"]
    if calls < 50:
        return None
    usd = sum(spend.run_spend(r) for r in db.q("select cost_estimate, models from runs where created_at>? and coalesce(loadtest,0)=0", (last,)))
    return {"since": last, "portal_calls": calls, "portal_usd": round(usd, 2)}


async def status(force=False):
    """The budget summary (cached for 5 minutes; force re-runs the query). Keeps the last good figures,
    marked stale, if Redash fails."""
    if not config.BUDGET_QUERY_ID:
        return None
    if not force and _cache["data"] and time.time() - _cache["at"] < CACHE_SEC:
        return _cache["data"]
    age = 0 if force else CACHE_SEC
    lookups = [auth.redash_rows(config.BUDGET_QUERY_ID, max_age=age)]
    if config.BUDGET_DAILY_QUERY_ID:
        lookups.append(auth.redash_rows(config.BUDGET_DAILY_QUERY_ID, max_age=age))
    results = await asyncio.gather(*lookups, return_exceptions=True)
    rows, day_rows = results[0], (results[1] if len(results) > 1 else [])
    if isinstance(day_rows, Exception):  # the chart is optional: keep the rest of the panel
        log.warning("daily budget lookup failed: %s", day_rows)
        day_rows = None
    try:
        if isinstance(rows, Exception):
            raise rows
    except Exception as error:  # noqa: BLE001 - the panel must never break the admin page
        log.warning("budget lookup failed: %s", error)
        if _cache["data"]:
            return {**_cache["data"], "stale": str(error)[:200]}
        return {"key_alias": config.BUDGET_KEY_ALIAS, "error": f"Couldn't read the budget: {str(error)[:200]}"}
    data = summarize(rows[0]) if rows else {"key_alias": config.BUDGET_KEY_ALIAS,
                                            "error": "The key wasn't found in LiteLLM's tables."}
    if rows:
        data["daily"] = daily(day_rows, data) if day_rows is not None else None
        data["log_gap"] = log_gap(data)
    _cache.update(at=time.time(), data=data)
    return data
