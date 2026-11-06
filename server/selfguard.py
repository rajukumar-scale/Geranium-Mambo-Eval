"""Keep the portal's own boat box running (2026-11-05, Evan: "how do we fix it? to avoid this").

On 11-05 boat stopped the server box twice: at 23:11 UTC (no request of ours on record) and again at 00:26, an hour
after it was resumed from the boat dashboard, which gives a resumed box a lifetime. Every review in flight then
failed. Two guards here, both cheap:
- every CHECK_EVERY, read the box from boat and, if it has a lifetime (auto-stop) again, turn it off. A stop that
  comes from the timer then can't happen, whoever resumed the box.
- when the portal starts on a box that has just booted (a resume or a reboot, not a deploy), tell the Slack alert
  channel, because while the box is down nothing on it can.
The outside watchdog (.github/workflows/watchdog.yml) resumes the box when it is stopped for any other reason.
"""

import asyncio
import json
import logging
import time

from . import config, notify

log = logging.getLogger("portal.selfguard")
CHECK_EVERY = 300
BOOT_FRESH_S = 900  # the box came up this recently: the portal start is a resume/reboot, not a deploy


def _uptime():
    try:
        return float(open("/proc/uptime").read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


async def check():
    """Turn the box's auto-stop off when it is on. Returns what it found (for logs and tests)."""
    box = config.SERVER_BOX_ID
    if not box or config.REVIEW_RUNNER != "boat":
        return {"skipped": True}
    from . import boatrun
    rc, out, err = await boatrun.cli("info", box, "--json", timeout=60)
    if rc != 0:
        log.warning("selfguard: boat info %s failed: %s", box, (err or out)[-200:])
        return {"error": "info"}
    try:
        sandbox = json.loads(out).get("sandbox") or {}
    except ValueError:
        return {"error": "json"}
    ttl, archive = sandbox.get("ttlSeconds"), sandbox.get("archiveAfter")
    if ttl is None and not archive:
        return {"ok": True}
    rc, out, err = await boatrun.cli("extend", box, "--no-auto-stop", "--json", timeout=60)
    if rc != 0:
        log.error("selfguard: could not turn auto-stop off on %s: %s", box, (err or out)[-200:])
        await notify.slack(f":warning: The eval portal's server box `{box}` has an auto-stop timer again "
                           f"(stops in about {round((ttl or 0) / 60)} min) and the portal couldn't turn it off. "
                           f"Run `boat extend {box} --no-auto-stop`.")
        return {"error": "extend", "ttl": ttl}
    log.warning("selfguard: %s had auto-stop on (ttl %s s, archiveAfter %s); turned it off", box, ttl, archive)
    await notify.slack(f":shield: The eval portal's server box `{box}` had an auto-stop timer again (it would have "
                       f"stopped in about {round((ttl or 0) / 60)} min, probably after a resume from the boat "
                       "dashboard). The portal turned it off; nothing else to do.")
    return {"fixed": True, "ttl": ttl}


async def announce_boot():
    up = _uptime()
    if up is not None and up < BOOT_FRESH_S:
        await notify.slack(f":rotating_light: The eval portal's server box just came back up ({round(up / 60)} min "
                           "ago): it had been stopped or restarted, so reviews running at the time may have failed. "
                           "The portal is answering again.")


async def loop():
    await asyncio.sleep(30)
    try:
        await announce_boot()
    except Exception:  # noqa: BLE001 - an alert must never stop the portal
        log.exception("selfguard boot notice failed")
    while True:
        try:
            await check()
        except Exception:  # noqa: BLE001
            log.exception("selfguard check failed")
        await asyncio.sleep(CHECK_EVERY)
