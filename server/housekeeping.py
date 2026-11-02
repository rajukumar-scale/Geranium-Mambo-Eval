"""Keeps the server's disk from filling up. On 2026-10-31 it hit 100% (147 GB) and every send failed with
"No space left on device": 8.6 GB of it was review-machine upload packages left behind by runs whose machine never
started, was cancelled or was cut off by a restart, and 18 GB was raw run logs. Every hour this:

- deletes upload packages (logs/<run>.payload.tgz, and the logs/<run>.payload folder it's built in) older than
  PAYLOAD_MAX_AGE: they're only read while a review machine starts;
- gzips run logs (logs/<run>.jsonl -> .jsonl.gz) of runs that finished more than LOG_GZIP_DAYS ago: the admin log
  link serves the .gz (app.admin_run_log); nothing else reads a finished run's log;
- cold-stores chats idle for COLD_HOURS: their home/ (Claude Code sessions) and cache/ packed into cold.tgz, unpacked
  again by workspace.ensure_attempt() the moment the chat runs anything (workspace.freeze / thaw);
- tells Slack when free space drops under ALERT_DISK_GB (at most every LOW_DISK_EVERY).
The disk grew 14.7 GB on 10-30 alone (sessions 6.6, run logs ~4, eval cache 2.7), so under LOW_DISK_GB free it works
harder: chats idle for COLD_HOURS_LOW_DISK and logs older than LOG_GZIP_DAYS_LOW_DISK.
2026-11-02 (Evan): 11 GB free with 12 GB of raw logs and 31 GB of open chats waiting on the old 3-day / 2-day limits, so
the normal limits are now 1 day / 12 h, the harder ones start at 15 GB, and Slack hears at 20 GB, with time to act.
"""

import asyncio
import gzip
import logging
import shutil
import time

from . import config, db, notify, workspace

log = logging.getLogger("portal.housekeeping")
EVERY = 900
PAYLOAD_MAX_AGE = 6 * 3600  # far longer than a machine takes to start (MACHINE_TRIES boots)
LOG_GZIP_DAYS = 1
LOG_GZIP_DAYS_LOW_DISK = 0.25
LOG_GZIP_BATCH = 2000  # logs per pass, so a backlog is worked through over a few hours
COLD_HOURS = 12  # a CB back after this waits a few seconds while the chat unpacks
COLD_HOURS_LOW_DISK = 6
FREEZE_BATCH = 60  # chats per pass (~125 MB each before packing)
LOW_DISK_GB = 15  # under this: the harder limits
ALERT_DISK_GB = 20  # under this: Slack
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


def gzip_logs(now=None, batch=LOG_GZIP_BATCH, days=LOG_GZIP_DAYS):
    """Compress the logs of runs that finished `days` ago or more. Returns (files, bytes) saved."""
    now = now or time.time()
    done, saved = 0, 0
    runs = db.q("select id, attempt_id from runs where finished_at is not null and finished_at < ? "
                "and status not in ('queued', 'running') order by finished_at", (now - days * 86400,))
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


def freeze_idle(now=None, idle=COLD_HOURS * 3600, batch=FREEZE_BATCH):
    """Cold-store chats with no activity for `idle` seconds and no queued or running run. Returns (chats, bytes) freed."""
    now = now or time.time()
    done, freed = 0, 0
    for chat in db.q("select attempt_id from chats where coalesce(last_activity, created_at, 0) < ? "
                     "order by last_activity", (now - idle,)):
        if done >= batch:
            break
        attempt = chat["attempt_id"]
        root = workspace.attempt_dir(attempt)
        if workspace.is_cold(attempt) or not any((root / d).is_dir() for d in workspace.COLD_DIRS):
            continue
        try:
            got = workspace.freeze(attempt, busy=lambda: bool(db.active_run(attempt)))
        except OSError as error:  # out of room for the archive: stop, the original files are untouched
            log.warning("couldn't cold-store %s: %s", attempt, error)
            break
        if got:
            done += 1
            freed += got
    return done, freed


def free_gb():
    return shutil.disk_usage(config.DATA_DIR).free / 1e9


async def check_disk(now=None):
    now = now or time.time()
    free = free_gb()
    if free < ALERT_DISK_GB and now - _state["alerted"] >= LOW_DISK_EVERY:
        _state["alerted"] = now
        await notify.slack(f"⚠️ Eval portal server disk is low: {free:.1f} GB free. At 0 every send fails "
                           f"(\"No space left on device\"). Biggest use: {config.DATA_DIR}/attempts.")
    return free


async def run_once():
    low = free_gb() < LOW_DISK_GB
    files, freed = await asyncio.to_thread(sweep_payloads)
    logs, saved = await asyncio.to_thread(gzip_logs, None, LOG_GZIP_BATCH, LOG_GZIP_DAYS_LOW_DISK if low else LOG_GZIP_DAYS)
    chats, packed = await asyncio.to_thread(freeze_idle, None, COLD_HOURS_LOW_DISK * 3600 if low else COLD_HOURS * 3600)
    free = await check_disk()
    if files or logs or chats:
        log.info("housekeeping%s: %d upload packages (%.1f GB), %d logs gzipped (%.1f GB), %d chats cold-stored (%.1f GB), "
                 "%.1f GB free", " (low disk)" if low else "", files, freed / 1e9, logs, saved / 1e9, chats, packed / 1e9, free)


async def loop():
    await asyncio.sleep(120)  # let startup and re-attached reviews settle first
    while True:
        try:
            await run_once()
        except Exception:  # noqa: BLE001 - housekeeping must never take the portal down
            log.exception("housekeeping failed")
        await asyncio.sleep(EVERY)
