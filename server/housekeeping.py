"""Keeps the server's disk from filling up. On 2026-10-31 it hit 100% (147 GB) and every send failed with
"No space left on device": 8.6 GB of it was review-machine upload packages left behind by runs whose machine never
started, was cancelled or was cut off by a restart, and 18 GB was raw run logs. Every hour this:

- deletes upload packages (logs/<run>.payload.tgz, and the logs/<run>.payload folder it's built in) older than
  PAYLOAD_MAX_AGE: they're only read while a review machine starts;
- gzips run logs (logs/<run>.jsonl -> .jsonl.gz) of runs that finished more than LOG_GZIP_DAYS ago: the admin log
  link serves the .gz (app.admin_run_log); nothing else reads a finished run's log;
- tells Slack when free space drops under LOW_DISK_GB (at most every LOW_DISK_EVERY).
"""

import asyncio
import gzip
import logging
import shutil
import time

from . import config, db, notify

log = logging.getLogger("portal.housekeeping")
EVERY = 3600
PAYLOAD_MAX_AGE = 6 * 3600  # far longer than a machine takes to start (MACHINE_TRIES boots)
LOG_GZIP_DAYS = 3
LOG_GZIP_BATCH = 2000  # logs per pass, so a backlog is worked through over a few hours
LOW_DISK_GB = 10
LOW_DISK_EVERY = 6 * 3600
_state = {"alerted": 0.0}


def sweep_payloads(now=None):
    """Delete stale upload packages. Returns (files, bytes) removed."""
    now = now or time.time()
    removed, freed = 0, 0
    attempts = config.DATA_DIR / "attempts"
    for path in attempts.glob("*/logs/*.payload*") if attempts.exists() else []:
        try:
            st = path.stat()
            if now - st.st_mtime < PAYLOAD_MAX_AGE:
                continue
            if path.is_dir() and path.name.endswith(".payload"):
                freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file() and path.name.endswith(".payload.tgz"):
                freed += st.st_size
                path.unlink()
            else:
                continue
            removed += 1
        except OSError:
            continue
    return removed, freed


def gzip_logs(now=None, batch=LOG_GZIP_BATCH):
    """Compress the logs of runs that finished LOG_GZIP_DAYS ago or more. Returns (files, bytes) saved."""
    now = now or time.time()
    done, saved = 0, 0
    runs = db.q("select id, attempt_id from runs where finished_at is not null and finished_at < ? "
                "and status not in ('queued', 'running') order by finished_at", (now - LOG_GZIP_DAYS * 86400,))
    for run in runs:
        if done >= batch:
            break
        src = config.DATA_DIR / "attempts" / run["attempt_id"] / "logs" / f"{run['id']}.jsonl"
        if not src.is_file():
            continue
        dst = src.with_name(src.name + ".gz")
        tmp = src.with_name(src.name + ".gz.tmp")
        try:
            with open(src, "rb") as fin, gzip.open(tmp, "wb", compresslevel=6) as fout:
                shutil.copyfileobj(fin, fout, 1 << 20)
            size = src.stat().st_size
            tmp.replace(dst)
            src.unlink()
            saved += size - dst.stat().st_size
            done += 1
        except OSError as error:  # a full disk mid-write: keep the original, drop the partial copy
            tmp.unlink(missing_ok=True)
            log.warning("couldn't gzip %s: %s", src, error)
            break
    return done, saved


def free_gb():
    return shutil.disk_usage(config.DATA_DIR).free / 1e9


async def check_disk(now=None):
    now = now or time.time()
    free = free_gb()
    if free < LOW_DISK_GB and now - _state["alerted"] >= LOW_DISK_EVERY:
        _state["alerted"] = now
        await notify.slack(f"⚠️ Eval portal server disk is low: {free:.1f} GB free. At 0 every send fails "
                           f"(\"No space left on device\"). Biggest use: {config.DATA_DIR}/attempts.")
    return free


async def run_once():
    files, freed = await asyncio.to_thread(sweep_payloads)
    logs, saved = await asyncio.to_thread(gzip_logs)
    free = await check_disk()
    if files or logs:
        log.info("housekeeping: %d upload packages (%.1f GB), %d logs gzipped (%.1f GB saved), %.1f GB free",
                 files, freed / 1e9, logs, saved / 1e9, free)


async def loop():
    await asyncio.sleep(120)  # let startup and re-attached reviews settle first
    while True:
        try:
            await run_once()
        except Exception:  # noqa: BLE001 - housekeeping must never take the portal down
            log.exception("housekeeping failed")
        await asyncio.sleep(EVERY)
