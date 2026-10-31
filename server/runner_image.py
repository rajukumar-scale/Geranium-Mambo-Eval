"""Keeps the review container image (RUNNER_IMAGE, built from runner/Dockerfile) present on this machine. On
2026-10-31 it vanished while the disk was full and every server review, follow-up and task-date save ended at once
with "Unable to find image 'eval-runner:latest' locally" for 15 minutes until it was rebuilt by hand. Now:
place() sends server reviews to boat while the image is missing, and loop() rebuilds it within a minute and tells
Slack."""

import asyncio
import logging
import subprocess
import time

from . import config, notify

log = logging.getLogger("portal.runner_image")
CHECK_EVERY = 60
BUILD_TIMEOUT = 1800
_state = {"checked": 0.0, "present": True, "building": False}


def _inspect():
    try:
        return subprocess.run(["docker", "image", "inspect", config.RUNNER_IMAGE], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return True  # docker itself is unreachable: rebuilding won't help, don't reroute on a guess


def present(max_age=30):
    """Is the image there? Cached for max_age seconds (place() asks for every review)."""
    if time.time() - _state["checked"] > max_age:
        _state.update(present=_inspect(), checked=time.time())
    return _state["present"]


async def rebuild():
    if _state["building"]:
        return False
    _state["building"] = True
    started = time.time()
    try:
        await notify.slack(f"⚠️ Eval portal: the review image {config.RUNNER_IMAGE} is missing on the server. Server "
                           "reviews go to boat meanwhile; rebuilding it now (about 2 minutes).")
        proc = await asyncio.create_subprocess_exec("docker", "build", "-t", config.RUNNER_IMAGE, str(config.ROOT / "runner"),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), BUILD_TIMEOUT)
        ok = proc.returncode == 0
        _state.update(present=_inspect(), checked=time.time())
        tail = out.decode(errors="replace")[-400:]
        log.log(logging.INFO if ok else logging.ERROR, "runner image rebuild %s in %.0fs: %s",
                "ok" if ok else "FAILED", time.time() - started, tail)
        await notify.slack(f"✅ Eval portal: rebuilt {config.RUNNER_IMAGE} in {time.time() - started:.0f}s." if ok and _state["present"]
                           else f"🚨 Eval portal: rebuilding {config.RUNNER_IMAGE} FAILED. Follow-ups and task-date saves fail "
                                f"until it's fixed (deploy/setup_vm.sh builds it). Last output: {tail[-250:]}")
        return ok
    except (OSError, asyncio.TimeoutError) as error:
        log.exception("runner image rebuild failed")
        await notify.slack(f"🚨 Eval portal: rebuilding {config.RUNNER_IMAGE} failed: {error}")
        return False
    finally:
        _state["building"] = False


async def loop():
    if config.RUNNER_MODE != "docker":
        return
    while True:
        try:
            if not await asyncio.to_thread(present, 0):
                log.error("runner image %s is missing; rebuilding", config.RUNNER_IMAGE)
                await rebuild()
        except Exception:  # noqa: BLE001 - the watcher must never take the portal down
            log.exception("runner image check failed")
        await asyncio.sleep(CHECK_EVERY)
