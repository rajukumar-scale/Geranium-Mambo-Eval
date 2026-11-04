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
import base64
import logging
import time

from . import boatrun, config, modalrun

log = logging.getLogger("portal.boxpool")

SCRUB_SH = r"""#!/bin/bash
# Wipe a review box between reviews (runs as root).
pkill -f /tmp/payload/memguard.sh 2>/dev/null || true
for i in 1 2 3; do pkill -9 -u runner 2>/dev/null || true; pgrep -u runner >/dev/null || break; sleep 1; done
crontab -r -u runner 2>/dev/null || true
rm -rf /tmp/payload /tmp/payload.tgz /tmp/evp_bundle.tgz /tmp/out.jsonl /tmp/err.txt /tmp/claude.pid /tmp/claude.exit /tmp/state_out.tgz \
       /tmp/scratch_out.tgz /tmp/memguard.log /work/Evals /work/claude /work/.claude /work/guidelines
for d in /tmp /var/tmp /dev/shm; do find "$d" -mindepth 1 -maxdepth 1 -user runner -exec rm -rf {} + 2>/dev/null || true; done
rm -rf /home/runner && mkdir -p /home/runner && cp -a /etc/skel/. /home/runner/ 2>/dev/null || true
chown -R runner:runner /home/runner && chmod 755 /home/runner
if pgrep -u runner >/dev/null || [ -n "$(find /tmp /var/tmp /dev/shm -mindepth 1 -maxdepth 1 -user runner 2>/dev/null)" ]; then
  echo "NOT CLEAN"; exit 1
fi
echo CLEAN
"""

_boxes = {}  # box id -> {"created": t, "snapshot": name, "reviews": n, "idle_since": t or None, "bundle": name}
_idle = []  # idle box ids, most recently released last
_lock = None
_stats = {"reused": 0, "created": 0, "retired": 0, "warmed": 0, "warm_failed": 0}
_demand = {"last": 0.0}  # when a review last asked for a box (warm boxes are kept only while they keep coming)
_warming = set()  # warm boxes being started right now (tasks)
WARM_AT_ONCE = 2  # warm boxes started at the same time, at most
EXTEND_EVERY_S = 1800  # an idle box's lifetime is pushed out this often (boat stops a box at its TTL)


def _get_lock():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def enabled():
    return config.BOX_REUSE_MAX > 0 and config.REVIEW_RUNNER != "modal"


def machines():
    """The review-machine backend: boat.dev boxes, or Modal sandboxes (modalrun, the same contract)."""
    return modalrun if config.REVIEW_RUNNER == "modal" else boatrun


def keep():
    """Boxes the pool holds: not orphans."""
    return set(_boxes)


async def _usable(box):
    info = _boxes.get(box) or {}
    if info.get("snapshot") != boatrun.active_snapshot():
        return False
    if time.time() - info.get("created", 0) > config.BOX_MAX_AGE_H * 3600:
        return False
    # One ssh (claim the wiped box) alongside the extend: each boat CLI call is a few seconds a review waits for.
    (rc, out), (ext_rc, _, _) = await asyncio.gather(
        boatrun.ssh(box, "test -e /tmp/evp_clean && sudo rm -f /tmp/evp_clean && echo READY", timeout=60),
        boatrun.cli("extend", box, "--ttl", str(config.RUN_TIMEOUT_MIN * 60 + 1800), "--json", timeout=60))
    return rc == 0 and "READY" in out and ext_rc == 0


async def acquire(progress=None):
    """A box for a new review: a clean idle one when there is one, else a new one. progress(text): told when boat
    refuses a start and it tries again."""
    _demand["last"] = time.time()
    if enabled():
        while True:
            async with _get_lock():
                box = _idle.pop() if _idle else None
            if not box:
                break
            if await _usable(box):
                _boxes[box]["idle_since"] = None
                _boxes[box]["extended"] = time.time()
                _stats["reused"] += 1
                log.info("reusing review box %s (review %d)", box, _boxes[box]["reviews"] + 1)
                return box
            await _retire(box, "not usable any more")
    box = await (boatrun.create_box(progress=progress) if machines() is boatrun else machines().create_box())
    _stats["created"] += 1
    if enabled():
        _boxes[box] = {"created": time.time(), "snapshot": boatrun.active_snapshot(), "reviews": 0, "idle_since": None}
    return box


async def _retire(box, why):
    _boxes.pop(box, None)
    _stats["retired"] += 1
    log.info("deleting review box %s: %s", box, why)
    await machines().delete_box(box)


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
        rc, out = await boatrun.ssh(box, scrub_command(), timeout=180)
    except Exception as error:  # noqa: BLE001
        rc, out = -1, str(error)
    if rc != 0 or "CLEAN" not in out or "NOT CLEAN" in out:
        return await _retire(box, f"wipe failed: {out[-200:]}")
    async with _get_lock():
        info["idle_since"] = time.time()
        _idle.append(box)


async def give_back(box):
    """A machine that was started (or claimed) for a review cancelled before it began: keep it, clean, for the next."""
    if not enabled():
        return await _retire(box, "review cancelled before it started")
    try:
        rc, out = await boatrun.ssh(box, "sudo touch /tmp/evp_clean && echo WARM", timeout=120)
    except Exception as error:  # noqa: BLE001
        rc, out = -1, str(error)
    if rc != 0 or "WARM" not in out:
        return await _retire(box, "couldn't keep it after a cancel")
    now = time.time()
    info = _boxes.setdefault(box, {"created": now, "snapshot": boatrun.active_snapshot(), "reviews": 0})
    info["idle_since"] = now
    info.setdefault("extended", now)
    async with _get_lock():
        if box not in _idle:
            _idle.append(box)
    _stats["given_back"] = _stats.get("given_back", 0) + 1
    log.info("kept review box %s for the next review (its review was cancelled while it started)", box)


def scrub_command():
    """The wipe, sent base64-encoded and run from stdin: passed as `bash -c <script>` its own command line contained
    "/tmp/payload/memguard.sh", so its `pkill -f` killed the wipe itself (ssh exit 255, 2026-11-01)."""
    script = (SCRUB_SH + "touch /tmp/evp_clean\n").encode()
    return f"echo {base64.b64encode(script).decode()} | base64 -d | sudo bash"


def has_bundle(box, name):
    """Whether this pool box already holds that eval bundle (RUN_SH keeps the last one in /var/cache/evp)."""
    return (_boxes.get(box) or {}).get("bundle") == name


def note_bundle(box, name):
    if box in _boxes:
        _boxes[box]["bundle"] = name


def stats():
    return {**_stats, "idle": len(_idle), "pool": len(_boxes), "warming": len(_warming)}


def busy(now=None):
    """Reviews are coming in: one asked for a box in the last BOX_WARM_BUSY_MIN minutes."""
    return (now or time.time()) - _demand["last"] < config.BOX_WARM_BUSY_MIN * 60


async def _warm_one():
    """Start a box ahead of demand and put it in the pool, clean. Its failures (boat's failed boots) cost no review."""
    try:
        box = await boatrun.create_box()
    except Exception as error:  # noqa: BLE001
        _stats["warm_failed"] += 1
        log.info("warm box didn't start: %s", str(error)[-200:])
        return
    try:
        rc, out = await boatrun.ssh(box, "sudo touch /tmp/evp_clean && echo WARM", timeout=120)
    except Exception as error:  # noqa: BLE001
        rc, out = -1, str(error)
    if rc != 0 or "WARM" not in out:
        _stats["warm_failed"] += 1
        await boatrun.delete_box(box)
        return
    now = time.time()
    _boxes[box] = {"created": now, "snapshot": boatrun.active_snapshot(), "reviews": 0, "idle_since": now,
                   "extended": now, "warm": True}
    async with _get_lock():
        _idle.insert(0, box)  # used after the boxes released by reviews (those already hold an eval bundle)
    _stats["warmed"] += 1
    log.info("warm review box %s ready (%d idle)", box, len(_idle))


async def tick(now=None):
    """One pass: keep the warm minimum while reviews come in, delete what's idle past BOX_IDLE_MIN beyond that, push
    out the lifetime of boxes that stay."""
    now = now or time.time()
    keep_warm = config.BOX_WARM_MIN if busy(now) else 0
    async with _get_lock():
        newest_first = sorted(_idle, key=lambda b: _boxes.get(b, {}).get("idle_since") or now, reverse=True)
        kept, stale = newest_first[:keep_warm], []
        for b in newest_first:
            info = _boxes.get(b) or {}
            outdated = info.get("snapshot") != boatrun.active_snapshot() or now - info.get("created", 0) > config.BOX_MAX_AGE_H * 3600
            idle_too_long = now - (info.get("idle_since") or now) > config.BOX_IDLE_MIN * 60
            if outdated or (b not in kept and idle_too_long):
                stale.append((b, "old snapshot or too old" if outdated else f"idle for {config.BOX_IDLE_MIN:g} minutes"))
                _idle.remove(b)
        kept = [b for b in kept if b in _idle]
    for b, why in stale:
        await _retire(b, why)
    for b in kept:
        info = _boxes.get(b) or {}
        if now - info.get("extended", info.get("created", now)) > EXTEND_EVERY_S:
            rc, out, err = await boatrun.cli("extend", b, "--ttl", str(config.RUN_TIMEOUT_MIN * 60 + 1800), "--json", timeout=60)
            if rc == 0:
                info["extended"] = now
    missing = keep_warm - len(_idle) - len(_warming)
    for _ in range(max(0, min(missing, WARM_AT_ONCE - len(_warming)))):
        task = asyncio.ensure_future(_warm_one())
        _warming.add(task)
        task.add_done_callback(_warming.discard)


async def loop():
    while True:
        await asyncio.sleep(30)
        if not enabled():
            continue
        try:
            await tick()
        except Exception:  # noqa: BLE001 - the pool must never take the portal down
            log.exception("box pool pass failed")
