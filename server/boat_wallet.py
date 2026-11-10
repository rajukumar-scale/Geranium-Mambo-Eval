"""Slack alert when the boat wallet runs low (Evan, 2026-11-01). On 11-01 the plan's included VM time ran out at 03:54
UTC with an empty wallet, so boat refused every new review machine and /prompt and /rubric hung at "Starting a fresh
review machine" until Evan topped up. Every CHECK_EVERY this reads the account with the portal's own boat CLI and posts
to the Slack alert channel when the wallet is under BOAT_WALLET_ALERT_USD or boat can't start machines, repeating every
REPEAT while it stays that way, with roughly how long it lasts; and once when it's back above the line. No dollar
amounts in the message (Evan, 2026-11-01).
2026-11-09 (Evan: "stop saying this in slack pls"): no more "running low" alerts. Slack hears only when boat really
can't start review machines (an outage: reviews hang), and once when it can again.
"""

import asyncio
import json
import logging
import time

from . import boatrun, config, notify

log = logging.getLogger("portal.boat_wallet")
CHECK_EVERY = 900
REPEAT = 6 * 3600
_state = {"low_since": None, "alerted": 0.0, "samples": []}  # samples: (time, wallet $) for the burn rate


def burn_per_hour(samples):
    """$ per hour over the last ~6 h of samples, counting only the drops (a top-up isn't negative spend)."""
    recent = [s for s in samples if s[0] >= samples[-1][0] - 6 * 3600] if samples else []
    spent = sum(max(0.0, a[1] - b[1]) for a, b in zip(recent, recent[1:]))
    hours = (recent[-1][0] - recent[0][0]) / 3600 if len(recent) > 1 else 0
    return spent / hours if hours >= 0.5 else None


def message(limits, wallet, rate):
    blocked = not limits.get("canStart", True)
    left = f" At the current pace it lasts about {wallet / rate:.0f} more hours." if rate and wallet > 0 and not blocked else ""
    head = "🚨 Eval portal: boat can't start review machines" if blocked else "⚠️ Eval portal: the boat wallet is running low"
    why = f" ({limits.get('blockedReason') or limits.get('startBlockedReason')})" if blocked else ""
    return (f"{head}{why}.{left} When it's empty, /prompt and /rubric hang at \"Starting a fresh review machine\". "
            "Top up: `boat wallet pay` or https://boat.dev/dashboard?tab=billing")


async def check(now=None):
    now = now or time.time()
    rc, out, err = await boatrun.cli("limits", "--json", timeout=60)
    if rc != 0:
        log.warning("boat limits failed: %s", (err or out)[-200:])
        return None
    limits = json.loads(out)
    wallet = float((limits.get("wallet") or {}).get("balanceDollars") or 0)
    _state["samples"] = (_state["samples"] + [(now, wallet)])[-60:]
    rate = burn_per_hour(_state["samples"])
    threshold = config.BOAT_WALLET_ALERT_USD
    low = not limits.get("canStart", True)  # only a real block alerts now; a low wallet alone doesn't (2026-11-09)
    if low:
        if _state["low_since"] is None:
            _state["low_since"] = now
        if now - _state["alerted"] >= REPEAT:
            _state["alerted"] = now
            await notify.slack(message(limits, wallet, rate))
    elif _state["low_since"] is not None:
        _state.update(low_since=None, alerted=0.0)
        await notify.slack("✅ Eval portal: boat can start review machines again.")
    return {"wallet": wallet, "rate": rate, "low": low, "under_threshold": wallet < threshold}


async def loop():
    if config.REVIEW_RUNNER != "boat" or not config.BOAT_WALLET_ALERT_USD:
        return
    await asyncio.sleep(60)
    while True:
        try:
            await check()
        except Exception:  # noqa: BLE001 - the watcher must never take the portal down
            log.exception("boat wallet check failed")
        await asyncio.sleep(CHECK_EVERY)
