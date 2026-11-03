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


def limit_for(attempt_id):
    """This attempt's budget: CB_SPEND_LIMIT_USD, or more if an admin approved more (attempt_budget). 0 = no limit."""
    base = config.CB_SPEND_LIMIT_USD
    if not base:
        return 0
    row = db.one("select limit_usd from attempt_budget where attempt_id=?", (attempt_id,))
    return max(base, (row or {}).get("limit_usd") or 0)


def status(chat):
    """The spend bar on a chat: this attempt's spend against its budget. over = new runs need an admin's approval."""
    spent = attempt_spend(chat["attempt_id"])
    flagged = flag(chat["attempt_id"])
    limit = limit_for(chat["attempt_id"])
    approved = db.one("select limit_usd, approved_by, approved_at from attempt_budget where attempt_id=?", (chat["attempt_id"],))
    return {**spent, "limit": limit, "base_limit": config.CB_SPEND_LIMIT_USD, "over": bool(limit) and spent["usd"] >= limit,
            "flagged_at": flagged["flagged_at"] if flagged else None, "approved": approved}


def over_budget(attempt_id):
    """(spent, limit) when this attempt has used its budget: no new run until an admin approves more (Evan, 2026-11-03)."""
    limit = limit_for(attempt_id)
    if not limit:
        return None
    spent = attempt_spend(attempt_id)["usd"]
    return (spent, limit) if spent >= limit else None


def approve(attempt_id, add_usd, by, note=""):
    """An admin lets this attempt spend add_usd more: the new budget is the higher of its budget and its spend, plus that."""
    spent = attempt_spend(attempt_id)["usd"]
    new_limit = round(max(limit_for(attempt_id), spent) + add_usd, 2)
    db.ex("insert or replace into attempt_budget(attempt_id, limit_usd, approved_by, approved_at, note) values (?,?,?,?,?)",
          (attempt_id, new_limit, by, time.time(), (note or "")[:300]))
    return new_limit, spent


BUDGET_NOTICE = ("💰 **Budget for this task: ${limit:,.0f}.** Every review and reply here costs model spend (a /prompt is about "
                 "$11, a /rubric about $23, a reply about $1). Plan before you run, fix everything a report raised before "
                 "you run it again, and **don't go over ${limit:,.0f}**. At ${limit:,.0f} new reviews and replies stop "
                 "until an admin approves more: type `/admin` with a short note if you need it.")


def maybe_notice(chat):
    """The budget warning, once per chat, the first time it is opened (existing chats get it on their next open)."""
    if not config.CB_SPEND_LIMIT_USD or not chat or chat.get("task_id") == "loadtest" or chat.get("archived_from"):
        return None
    if db.one("select 1 from messages where attempt_id=? and role='system' and meta like '%\"budget_notice\"%'",
              (chat["attempt_id"],)):
        return None
    msg = db.add_message(chat["attempt_id"], "system", BUDGET_NOTICE.format(limit=limit_for(chat["attempt_id"])),
                         {"budget_notice": True, "warn": True})
    hub.publish(chat["attempt_id"], "message", msg)
    return msg


def flagged_attempts():
    return {r["attempt_id"]: r for r in db.q("select * from attempt_spend_flags")}


async def check_limit(attempt_id):
    """After each run: the first time an attempt's spend reaches its budget (again after each approved raise), flag it,
    tell the contributor that new runs now need an admin's approval, and tell the admins."""
    limit = limit_for(attempt_id)
    chat = db.get_chat(attempt_id)
    if not limit or not chat or chat.get("task_id") == "loadtest":
        return
    spent = attempt_spend(attempt_id)["usd"]
    if spent < limit:
        return
    with db._lock:  # two runs can finish at the same moment: flag (and warn) once per budget
        old = flag(attempt_id)
        if old and (old["limit_usd"] or 0) >= limit:
            return
        db.ex("insert or replace into attempt_spend_flags(attempt_id, user_id, email, spend_usd, limit_usd, flagged_at)"
              " values (?,?,?,?,?,?)", (attempt_id, chat["user_id"], chat["email"], spent, limit, time.time()))
    log.warning("attempt %s reached its budget: $%.2f of $%.0f", attempt_id, spent, limit)
    msg = db.add_message(attempt_id, "system",
                         f"⛔ **Budget reached.** This task's eval spend is now **${spent:,.2f}** of its **${limit:,.0f}** "
                         "budget, so new reviews and replies are paused. The admins have been told. If you need more, "
                         "type `/admin` with a short note on what's left to do; an admin can approve more budget.",
                         {"spend_flag": True})
    hub.publish(attempt_id, "message", msg)
    hub.publish(attempt_id, "spend", status(chat))
    detail = f" · {chat['email']}" if config.SLACK_ALERT_DETAIL else ""
    sent = await notify.slack(f":triangular_flag_on_post: An attempt reached its ${limit:,.0f} eval budget; its runs are paused until an admin approves more "
                              f"(${spent:,.2f}, attempt `{attempt_id[:8]}…`{detail}). "
                              f"Open it on the admin page: {notify.admin_link(attempt_id)}")
    db.update("attempt_spend_flags", "attempt_id", attempt_id, notified=int(bool(sent)))
