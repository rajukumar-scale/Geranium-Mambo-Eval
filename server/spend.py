"""Model spend: what a run cost, each attempt's running total, and the over-the-limit flag."""

import json
import logging
import time

from . import config, db, hub, notify

log = logging.getLogger("portal.spend")

# List prices in USD per million tokens (input, output, cache read, 5-minute cache write), only used
# when Claude Code reports no cost (crash/timeout) and for a run still in progress. Opus 5.5 and
# Haiku 5.5 were solved exactly from Claude Code's own costUSD; unknown models fall back to Opus rates.
FALLBACK_PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def table_cost(proxy_models):
    total = 0.0
    for model, m in (proxy_models or {}).items():
        price = FALLBACK_PRICES.get(model) or FALLBACK_PRICES["claude-opus-5-5"]
        total += (m.get("input", 0) * price[0] + m.get("output", 0) * price[1] +
                  m.get("cache_read", 0) * price[2] + m.get("cache_creation", 0) * price[3]) / 1e6
    return total


def run_spend(run):
    """Claude Code's own cost for the run; for a run still going, or one that died before reporting
    it, price the tokens the proxy metered so far."""
    if run["cost_estimate"] is not None:
        return run["cost_estimate"]
    models = run["models"]
    return table_cost(json.loads(models or "{}") if isinstance(models, str) else models)


def attempt_spend(attempt_id):
    """What this attempt's own runs cost, including a run in progress. The limit is per attempt, so a
    new attempt starts at $0. Runs an admin starts on it are billed to the admin, so they don't count."""
    rows = db.q("select cost_estimate, models, status from runs where attempt_id=? "
                "and coalesce(actor_admin,0)=0 and coalesce(loadtest,0)=0", (attempt_id,))
    return {"usd": round(sum(run_spend(r) for r in rows), 2),
            "running": any(r["status"] in ("queued", "running") for r in rows), "runs": len(rows)}


def attempt_totals():
    """{attempt id: {usd, user_id, email}} for every attempt, measured the way the limit is."""
    out = {}
    for r in db.q("select r.attempt_id, r.cost_estimate, r.models, c.user_id, c.email from runs r "
                  "left join chats c on c.attempt_id=r.attempt_id "
                  "where coalesce(r.actor_admin,0)=0 and coalesce(r.loadtest,0)=0"):
        t = out.setdefault(r["attempt_id"], {"usd": 0.0, "user_id": r["user_id"], "email": r["email"]})
        t["usd"] += run_spend(r)
    for t in out.values():
        t["usd"] = round(t["usd"], 2)
    return out


def flag(attempt_id):
    return db.one("select * from attempt_spend_flags where attempt_id=?", (attempt_id,))


def status(chat):
    """The spend bar on a chat: this attempt's spend against the limit."""
    spent = attempt_spend(chat["attempt_id"])
    flagged = flag(chat["attempt_id"])
    return {**spent, "limit": config.CB_SPEND_LIMIT_USD, "over": bool(config.CB_SPEND_LIMIT_USD) and
            spent["usd"] >= config.CB_SPEND_LIMIT_USD, "flagged_at": flagged["flagged_at"] if flagged else None}


def flagged_attempts():
    return {r["attempt_id"]: r for r in db.q("select * from attempt_spend_flags")}


async def check_limit(attempt_id):
    """After each run: the first time an attempt's spend reaches the limit, flag it, warn the contributor
    in that chat and tell the admins. Runs are never blocked."""
    limit = config.CB_SPEND_LIMIT_USD
    chat = db.get_chat(attempt_id)
    if not limit or not chat or chat.get("task_id") == "loadtest":
        return
    spent = attempt_spend(attempt_id)["usd"]
    if spent < limit:
        return
    with db._lock:  # two runs can finish at the same moment: flag (and warn) once
        if flag(attempt_id):
            return
        db.ex("insert into attempt_spend_flags(attempt_id, user_id, email, spend_usd, limit_usd, flagged_at)"
              " values (?,?,?,?,?,?)", (attempt_id, chat["user_id"], chat["email"], spent, limit, time.time()))
    log.warning("attempt %s is over the spend limit: $%.2f of $%.0f", attempt_id, spent, limit)
    msg = db.add_message(attempt_id, "system",
                         f"⚠️ **Spend limit reached.** This attempt's eval spend is now **${spent:,.2f}**, over the "
                         f"**${limit:,.0f}** limit for each attempt. Your account has been flagged and the admins have "
                         "been told. You'll be warned, and your account may be disabled. Type `/admin` with a short "
                         "note if you think this is a mistake.", {"spend_flag": True})
    hub.publish(attempt_id, "message", msg)
    hub.publish(attempt_id, "spend", status(chat))
    detail = f" · {chat['email']}" if config.SLACK_ALERT_DETAIL else ""
    sent = await notify.slack(f":triangular_flag_on_post: An attempt went over the ${limit:,.0f} eval spend limit "
                              f"(${spent:,.2f}, attempt `{attempt_id[:8]}…`{detail}). "
                              f"Open it on the admin page: {notify.admin_link(attempt_id)}")
    db.update("attempt_spend_flags", "attempt_id", attempt_id, notified=int(bool(sent)))
