"""Admin notifications. Slack incoming webhook, or a bot token + channel."""

import logging

import httpx

from . import config

log = logging.getLogger("portal.notify")


def configured():
    return bool(config.SLACK_WEBHOOK_URL or (config.SLACK_BOT_TOKEN and config.SLACK_CHANNEL))


async def slack(text):
    if not configured():
        log.warning("Slack is not configured; admin notification not sent: %s", text[:200])
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if config.SLACK_WEBHOOK_URL:
                resp = await client.post(config.SLACK_WEBHOOK_URL, json={"text": text})
                ok = resp.status_code == 200
            else:
                resp = await client.post("https://slack.com/api/chat.postMessage",
                                         headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
                                         json={"channel": config.SLACK_CHANNEL, "text": text, "unfurl_links": False})
                ok = resp.json().get("ok", False)
        if not ok:
            log.warning("Slack notification failed: %s %s", resp.status_code, resp.text[:300])
        return ok
    except httpx.HTTPError as error:
        log.warning("Slack notification error: %s", error)
        return False


def admin_link(attempt_id):
    return f"{config.PUBLIC_BASE_URL}/admin#{attempt_id}"
