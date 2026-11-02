"""Write down what the portal was doing when it froze (2026-11-02: three stalls in a day while boat snapshotted the
disk; each time we could only guess afterwards). A tick on the event loop every 0.5 s; a thread checks it, and when the
loop hasn't ticked for STALL_S seconds it writes every thread's stack, the load and the disk pressure to
DATA/stalls/<time>.txt (once per stall, the newest KEEP kept)."""

import logging
import os
import sys
import threading
import time
import traceback

from . import config

log = logging.getLogger("portal.stallwatch")
STALL_S = 3.0
KEEP = 50
_state = {"tick": time.monotonic(), "started": False}


def _tick(loop):
    _state["tick"] = time.monotonic()
    loop.call_later(0.5, _tick, loop)


def snapshot(lag):
    lines = [f"event loop stalled {lag:.1f} s at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC"]
    for name in ("/proc/loadavg", "/proc/pressure/io", "/proc/pressure/memory", "/proc/pressure/cpu"):
        try:
            lines.append(f"{name}: {open(name).read().strip()}")
        except OSError:
            pass
    names = {t.ident: t.name for t in threading.enumerate()}
    for ident, frame in sys._current_frames().items():
        lines.append(f"\n--- thread {names.get(ident, ident)}")
        lines.extend(l.rstrip() for l in traceback.format_stack(frame))
    return "\n".join(lines) + "\n"


def _write(text):
    folder = config.DATA_DIR / "stalls"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("%Y%m%dT%H%M%SZ.txt", time.gmtime())
    path.write_text(text)
    for old in sorted(folder.glob("*.txt"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return path


def _watch():
    in_stall, path, worst = False, None, 0.0
    while True:
        time.sleep(1)
        lag = time.monotonic() - _state["tick"]
        if lag >= STALL_S and not in_stall:
            in_stall, worst = True, lag
            try:
                path = _write(snapshot(lag))
                log.warning("event loop stalled %.1f s; stacks in %s", lag, path)
            except Exception:  # noqa: BLE001
                log.exception("couldn't record a stall")
        elif in_stall:
            worst = max(worst, lag)
            if lag < 1:  # over: how long it really lasted (the file is written when it crosses STALL_S)
                in_stall = False
                log.warning("event loop stall over: %.1f s", worst)
                if path:
                    try:
                        with open(path, "a") as f:
                            f.write(f"\nstall over after {worst:.1f} s\n")
                    except OSError:
                        pass


def start(loop):
    if _state["started"] or os.environ.get("EVP_NO_STALLWATCH"):
        return
    _state["started"] = True
    _state["tick"] = time.monotonic()
    loop.call_soon(_tick, loop)
    threading.Thread(target=_watch, name="stallwatch", daemon=True).start()
