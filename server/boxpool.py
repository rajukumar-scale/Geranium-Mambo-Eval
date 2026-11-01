"""Reused review machines (Evan, 2026-11-01). Each review used to get a brand-new boat box: about 1,960 starts a day
against the plan's 840 (1,126 extra at $0.03-0.07 each), plus a 1-3 minute boot and an ~8% chance the boot failed.
Now a box that finished a review cleanly is wiped and kept for the next one:

- acquire(): the most recently used idle box that still answers and runs on the current runner snapshot, its
  lifetime extended; else a new box (boatrun.create_box).
- release(box, clean): after a clean review (it ended normally, no timeout, cancel, lost contact or setup error) the
  box is wiped (SCRUB_SH) and goes back to the pool, unless it has done BOX_REUSE_MAX reviews, is older than
  BOX_MAX_AGE_H or was made from an older snapshot. Anything else is deleted, as before.
- An idle box unused for BOX_IDLE_MIN is deleted (idle boxes cost VM time). The pool lives in memory: after a portal
  restart, the startup sweep (boatrun.sweep_orphans) deletes the idle ones.

The wipe removes everything one review leaves for the next: the runner user's processes, its home (Claude Code is
installed system-wide, in /usr/local/bin), the eval files (RUN_SH unpacks a fresh bundle every run), the payload with
the old run token, its scratch files under /tmp, /var/tmp and /dev/shm, and any crontab. It runs as root and must
print CLEAN; otherwise the box is deleted. BOX_REUSE_MAX=0 turns reuse off.
"""

import asyncio
import logging
import shlex
import time

from . import boatrun, config

log = logging.getLogger("portal.boxpool")

SCRUB_SH = r"""#!/bin/bash
# Wipe a review box between reviews (runs as root).
pkill -f /tmp/payload/memguard.sh 2>/dev/null || true
for i in 1 2 3; do pkill -9 -u runner 2>/dev/null || true; pgrep -u runner >/dev/null || break; sleep 1; done
crontab -r -u runner 2>/dev/null || true
rm -rf /tmp/payload /tmp/payload.tgz /tmp/out.jsonl /tmp/err.txt /tmp/claude.pid /tmp/claude.exit /tmp/state_out.tgz \
       /tmp/scratch_out.tgz /tmp/memguard.log /work/Evals /work/claude /work/.claude /work/guidelines
for d in /tmp /var/tmp /dev/shm; do find "$d" -mindepth 1 -maxdepth 1 -user runner -exec rm -rf {} + 2>/dev/null || true; done
rm -rf /home/runner && mkdir -p /home/runner && cp -a /etc/skel/. /home/runner/ 2>/dev/null || true
chown -R runner:runner /home/runner && chmod 755 /home/runner
if pgrep -u runner >/dev/null || [ -n "$(find /tmp /var/tmp /dev/shm -mindepth 1 -maxdepth 1 -user runner 2>/dev/null)" ]; then
  echo "NOT CLEAN"; exit 1
fi
echo CLEAN
"""

_boxes = {}  # box id -> {"created": t, "snapshot": name, "reviews": n, "idle_since": t or None}
_idle = []  # idle box ids, most recently released last
_lock = None
_stats = {"reused": 0, "created": 0, "retired": 0}


def _get_lock():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def enabled():
    return config.BOX_REUSE_MAX > 0


def keep():
    """Boxes the pool holds: not orphans."""
    return set(_boxes)


async def _usable(box):
    info = _boxes.get(box) or {}
    if info.get("snapshot") != boatrun.active_snapshot():
        return False
    if time.time() - info.get("created", 0) > config.BOX_MAX_AGE_H * 3600:
        return False
    rc, out = await boatrun.ssh(box, "test -e /tmp/evp_clean && echo READY", timeout=60)
    if rc != 0 or "READY" not in out:
        return False
    rc, out, err = await boatrun.cli("extend", box, "--ttl", str(config.RUN_TIMEOUT_MIN * 60 + 1800), "--json", timeout=60)
    return rc == 0


async def acquire():
    """A box for a new review: a clean idle one when there is one, else a new one."""
    if enabled():
        while True:
            async with _get_lock():
                box = _idle.pop() if _idle else None
            if not box:
                break
            if await _usable(box):
                _boxes[box]["idle_since"] = None
                await boatrun.ssh(box, "sudo rm -f /tmp/evp_clean", timeout=60)
                _stats["reused"] += 1
                log.info("reusing review box %s (review %d)", box, _boxes[box]["reviews"] + 1)
                return box
            await _retire(box, "not usable any more")
    box = await boatrun.create_box()
    _stats["created"] += 1
    if enabled():
        _boxes[box] = {"created": time.time(), "snapshot": boatrun.active_snapshot(), "reviews": 0, "idle_since": None}
    return box


async def _retire(box, why):
    _boxes.pop(box, None)
    _stats["retired"] += 1
    log.info("deleting review box %s: %s", box, why)
    await boatrun.delete_box(box)


async def release(box, clean):
    """A review on this box is over: wipe it and keep it, or delete it."""
    info = _boxes.get(box)
    if not (enabled() and clean and info):
        return await _retire(box, "review didn't end cleanly" if info else "not a pool box")
    info["reviews"] += 1
    if info["reviews"] >= config.BOX_REUSE_MAX:
        return await _retire(box, f"done {info['reviews']} reviews")
    if info["snapshot"] != boatrun.active_snapshot() or time.time() - info["created"] > config.BOX_MAX_AGE_H * 3600:
        return await _retire(box, "old snapshot or too old")
    try:
        rc, out = await boatrun.ssh(box, "sudo bash -c " + shlex.quote(SCRUB_SH + "touch /tmp/evp_clean\n"), timeout=180)
    except Exception as error:  # noqa: BLE001
        rc, out = -1, str(error)
    if rc != 0 or "CLEAN" not in out or "NOT CLEAN" in out:
        return await _retire(box, f"wipe failed: {out[-200:]}")
    async with _get_lock():
        info["idle_since"] = time.time()
        _idle.append(box)


def stats():
    return {**_stats, "idle": len(_idle), "pool": len(_boxes)}


async def loop():
    """Delete boxes idle for longer than BOX_IDLE_MIN."""
    while True:
        await asyncio.sleep(60)
        if not enabled():
            continue
        now = time.time()
        async with _get_lock():
            stale = [b for b in _idle if now - (_boxes.get(b, {}).get("idle_since") or now) > config.BOX_IDLE_MIN * 60]
            for b in stale:
                _idle.remove(b)
        for b in stale:
            await _retire(b, f"idle for {config.BOX_IDLE_MIN} minutes")
