"""DM Evan on Slack when a contributor is stuck on a fingerprint linter at L-1.

Every LINTER_ALERT_EVERY_MIN minutes this runs the saved Redash query LINTER_ALERT_QUERY_ID ("MCP-AC ·
fingerprint linter · stuck now", 395304: the latest "… fingerprint must match the pass screen" check failed,
3+ fails since the last pass, checked in the last 90 min) and DMs LINTER_ALERT_SLACK_USER the CBs it hasn't
told him about in the last LINTER_ALERT_REPEAT_HOURS. A DM needs the Slack bot token (an incoming webhook
can only post to its channel). On the first start it sends a one-time "alerts are on" DM; if that can't be
sent it says so once in the portal's alert channel, so a missing token doesn't go unnoticed.
"""

import asyncio
import logging
import time

import httpx

from . import auth, config, db, notify

log = logging.getLogger("portal.linter_alerts")


def _sent(key):
    return db.one("select created_at from status_notified where key=?", (key,))


def _mark(key):
    db.ex("insert or replace into status_notified(key, created_at) values (?,?)", (key, time.time()))


async def dm(text):
    """Post a DM to LINTER_ALERT_SLACK_USER with the bot token. False if it can't."""
    if not (config.SLACK_BOT_TOKEN and config.LINTER_ALERT_SLACK_USER):
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post("https://slack.com/api/chat.postMessage",
                                     headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
                                     json={"channel": config.LINTER_ALERT_SLACK_USER, "text": text, "unfurl_links": False})
        ok = resp.json().get("ok", False)
        if not ok:
            log.warning("linter DM failed: %s", resp.text[:300])
        return ok
    except (httpx.HTTPError, ValueError) as error:
        log.warning("linter DM error: %s", error)
        return False


def message(rows):
    many = len(rows) != 1
    lines = [f"⚠️ {len(rows)} CB{'s' if many else ''} stuck on a fingerprint linter (L-1, Atlas Commercial):"]
    for r in rows:
        lines.append(f"• {r.get('STAGE')} · {r.get('EMAIL') or r.get('WORKER_ID')} · task {r.get('TASK_ID')} · "
                     f"{r.get('FAILS_SINCE_PASS')} fails over {r.get('MINUTES_SINCE_FIRST_FAIL')} min")
    lines.append(f"Dashboard: {config.LINTER_ALERT_DASHBOARD_URL}")
    return "\n".join(lines)


def _key(row):
    return f"linterdm:{row.get('STAGE')}:{row.get('TASK_ID')}:{row.get('WORKER_ID')}"


async def check_once():
    rows = await auth.redash_rows(config.LINTER_ALERT_QUERY_ID, max_age=300)
    now = time.time()
    fresh = [r for r in rows
             if not (_sent(_key(r)) and now - _sent(_key(r))["created_at"] < config.LINTER_ALERT_REPEAT_HOURS * 3600)]
    if fresh and await dm(message(fresh)):
        for r in fresh:
            _mark(_key(r))
    return fresh


async def _hello():
    if _sent("linterdm:hello") or _sent("linterdm:cant-dm"):
        return
    if await dm("👋 Fingerprint linter alerts are on. I'll DM you here when a CB is stuck on a "
                "\"… fingerprint must match the pass screen\" linter (3+ fails since their last pass). "
                f"Dashboard: {config.LINTER_ALERT_DASHBOARD_URL}"):
        _mark("linterdm:hello")
    else:
        _mark("linterdm:cant-dm")
        await notify.slack("⚠️ The linter-stuck DM alerts can't DM Evan: the portal has no Slack bot token "
                           "(SLACK_BOT_TOKEN), only a webhook. They stay off until one is set.")


async def loop():
    if not config.LINTER_ALERT_QUERY_ID:
        return
    await asyncio.sleep(90)  # let a deploy settle first
    try:
        await _hello()
    except Exception:
        log.exception("linter alert hello failed")
    while True:
        try:
            if config.SLACK_BOT_TOKEN:
                await check_once()
        except Exception:
            log.exception("linter alert check failed")
        await asyncio.sleep(config.LINTER_ALERT_EVERY_MIN * 60)
