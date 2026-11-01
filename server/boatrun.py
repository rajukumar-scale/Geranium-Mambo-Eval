"""Per-review boat.dev boxes.

Each review gets a fresh box cloned from the `hg-runner` snapshot, which holds Claude Code and
the universes at /work; snapshot forks share the disk, so the universe is never copied. The
server uploads one payload (eval bundle + this attempt's saved state + the run command), the
review runs as the unprivileged `runner` user, output streams back over `boat ssh` (with
reconnects), and the attempt's state comes back as a tarball before the box is deleted.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import tarfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config, workspace

log = logging.getLogger("portal.boat")
_bundle_cache = {}  # release path + mtime -> tarball path
_bundle_lock = threading.Lock()  # many reviews starting at once must not pack the same bundle in parallel
_start_lock = None
_next_start = 0.0


async def _start_slot():
    """Space box requests to the plan's per-minute limit, so a burst of reviews queues in order
    instead of all retrying against boat's limit at once."""
    global _start_lock, _next_start
    if _start_lock is None:
        _start_lock = asyncio.Lock()
    async with _start_lock:
        now = time.monotonic()
        wait = _next_start - now
        _next_start = max(now, _next_start) + 60 / max(1, config.BOAT_STARTS_PER_MIN)
    if wait > 0:
        await asyncio.sleep(wait)

RUN_SH = r"""#!/bin/bash
# Prepare the box and start the review in the background. Runs as root. Safe to re-run: if the review
# already started (a retried setup step), it only reports that.
set -euo pipefail
if [ -s /tmp/claude.pid ] && { [ -e /tmp/claude.exit ] || kill -0 "$(cat /tmp/claude.pid)" 2>/dev/null; }; then
  echo STARTED; exit 0
fi
P=/tmp/payload
tar xzf $P/bundle.tgz -C /
mkdir -p /work/Evals/task_inputs /work/Evals/.cache /home/runner
tar xzf $P/state.tgz -C /
chown -R root:root /work/Evals /work/claude /work/.claude
chmod -R a+rX,go-w /work/Evals /work/claude /work/.claude
chown -R runner:runner /work/Evals/task_inputs /work/Evals/.cache /home/runner
if [ -s $P/scratch.tgz ]; then  # continuing a review paused by the time limit: put back its /tmp scratch files
  for n in $(tar tzf $P/scratch.tgz | cut -d/ -f1 | sort -u); do rm -rf "/tmp/$n"; done
  tar xzf $P/scratch.tgz -C /tmp --no-same-owner
  for n in $(tar tzf $P/scratch.tgz | cut -d/ -f1 | sort -u); do chown -R runner:runner "/tmp/$n"; done
fi
chmod 711 $P; chmod 600 $P/bundle.tgz $P/state.tgz
chown runner:runner $P/run.env $P/cmd.sh $P/prompt.txt; chmod 600 $P/run.env $P/prompt.txt
if [ -e $P/resume.sh ]; then chown runner:runner $P/resume.sh $P/resume_prompt.txt; chmod 600 $P/resume_prompt.txt; fi

if [ -s $P/proxy_host ]; then  # the runner user may reach only the metering proxy (and DNS)
  H=$(cat $P/proxy_host)
  for fam in 4 6; do
    T=iptables; [ $fam = 6 ] && T=ip6tables
    $T -N EVP_RUNNER 2>/dev/null || $T -F EVP_RUNNER
    for ns in $(awk '/^nameserver/{print $2}' /etc/resolv.conf); do
      case "$ns" in *:*) [ $fam = 6 ] || continue ;; *) [ $fam = 4 ] || continue ;; esac
      $T -A EVP_RUNNER -d "$ns" -p udp --dport 53 -j ACCEPT
      $T -A EVP_RUNNER -d "$ns" -p tcp --dport 53 -j ACCEPT
    done
    for ip in $(getent ahostsv$fam "$H" | awk '{print $1}' | sort -u); do
      $T -A EVP_RUNNER -d "$ip" -p tcp --dport 443 -j ACCEPT
    done
    $T -A EVP_RUNNER -j REJECT
    $T -C OUTPUT -m owner --uid-owner runner -j EVP_RUNNER 2>/dev/null || $T -I OUTPUT 1 -m owner --uid-owner runner -j EVP_RUNNER
  done
fi

# Boat installs and updates Claude Code itself, in the background after the machine boots (seen rewriting it 7
# minutes in); a review started meanwhile dies with "claude native binary not installed". Wait until it runs;
# if it never does, say so (no STARTED) and the server replaces the machine.
if [ -e $P/check.sh ]; then
  chown runner:runner $P/check.sh
  ready=0
  for i in $(seq 1 24); do
    if timeout 30 sudo -u runner -H bash $P/check.sh > /dev/null 2>&1; then ready=1; break; fi
    sleep 5
  done
  if [ $ready = 0 ]; then echo "CLAUDE_NOT_READY: Claude Code didn't run on this machine within 2 minutes"; exit 0; fi
fi

rm -f /tmp/out.jsonl /tmp/err.txt /tmp/claude.exit
touch /tmp/out.jsonl /tmp/err.txt; chmod 644 /tmp/out.jsonl /tmp/err.txt
setsid nohup bash /tmp/payload/review.sh > /dev/null 2>&1 < /dev/null &
echo $! > /tmp/claude.pid
pkill -f /tmp/payload/memguard.sh 2>/dev/null || true
setsid nohup bash /tmp/payload/memguard.sh > /dev/null 2>&1 < /dev/null &
echo STARTED
"""

# Runs as root: the review itself, as the runner user. If the model connection fails (the run ends with "API Error:
# Connection closed mid-response", first seen 2026-10-26, or, once Claude Code's own retries run out, "API Error:
# Connection refused ... (ECONNREFUSED)" or "API Error: Can't reach the API server ... (ENOTFOUND)", both seen in
# bursts on boat's network 2026-10-29), resume the same session on this
# machine, up to 3 times with a growing wait (1, 2, 3 minutes) so a burst can pass, so the work so far is kept instead
# of failing a review that may be 30 minutes in. The output stream and the exit file carry on as one run, so the
# server reads it like any other (it keeps the last result line).
REVIEW_SH = r"""#!/bin/bash
P=/tmp/payload
sudo -u runner -H bash $P/cmd.sh >> /tmp/out.jsonl 2>> /tmp/err.txt; rc=$?
n=0
while [ -e $P/resume.sh ] && [ $n -lt 3 ] && tail -n 1 /tmp/out.jsonl | grep -q '"is_error":true' \
   && tail -n 3 /tmp/out.jsonl | grep -qE 'API Error: Connection (closed mid-response|refused)|ECONNREFUSED|ENOTFOUND'; do
  n=$((n+1))
  echo "$(date -u +%FT%TZ) model connection failed; resuming the session ($n of 3) in $((60 * n))s" >> /tmp/err.txt
  sleep $((60 * n))
  sudo -u runner -H bash $P/resume.sh >> /tmp/out.jsonl 2>> /tmp/err.txt; rc=$?
done
echo $rc > /tmp/claude.exit
"""

RESUME_PROMPT = ("The connection to the model failed in the middle of your last step (a dropped or refused "
                 "connection). Continue exactly where you left off: don't redo steps that already finished, and "
                 "redo the step that was cut off only if its result is missing. Then finish the task as instructed.")

# Runs as root next to the review. When the machine is about to run out of memory (seen: parallel recheck passes
# each json.load-ing the 2.6 GB Snowflake export froze an 8 GB box for 10 minutes), kill the review's biggest
# Python process, so the machine stays responsive and the model sees "Killed" instead of everything hanging.
MEMGUARD_SH = r"""#!/bin/bash
LIMIT_KB=${MEMGUARD_MIN_KB:-650000}
while [ ! -e /tmp/claude.exit ]; do
  avail=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
  if [ "${avail:-99999999}" -lt "$LIMIT_KB" ]; then
    pid=$(ps -u runner -o pid=,rss=,comm= --sort=-rss | awk '$3 ~ /^python/ {print $1; exit}')
    if [ -n "$pid" ]; then
      kill -9 "$pid" && echo "$(date -u +%FT%TZ) low memory (${avail} kB free): killed python $pid" >> /tmp/memguard.log
    fi
  fi
  sleep 1
done
"""

COLLECT_SH = r"""#!/bin/bash
# Pack this attempt's state for the server. Runs as root. Also the review's scratch files under /tmp (whatever the
# runner user made there), so a review paused by the time limit can continue with them; skipped above 50 MB.
cd /
tar czf /tmp/state_out.tgz work/Evals/task_inputs work/Evals/.cache home/runner 2>/dev/null || true
chmod 644 /tmp/state_out.tgz
cd /tmp
rm -f /tmp/scratch_out.tgz
names=$(find /tmp -mindepth 1 -maxdepth 1 -user runner -printf '%f\n' 2>/dev/null)
if [ -n "$names" ] && [ "$(du -scm $names 2>/dev/null | tail -1 | cut -f1)" -le 50 ]; then
  tar czf /tmp/scratch_out.tgz $names 2>/dev/null || rm -f /tmp/scratch_out.tgz
  [ -e /tmp/scratch_out.tgz ] && chmod 644 /tmp/scratch_out.tgz
fi
"""


def active_snapshot():
    """The image new review boxes start from: the last one an eval-files update built, else BOAT_SNAPSHOT."""
    try:
        name = (config.DATA_DIR / "runner_snapshot").read_text().strip()
    except OSError:
        name = ""
    return name or config.BOAT_SNAPSHOT


def _kept_boxes():
    try:
        return set((config.DATA_DIR / "update" / "keep_boxes").read_text().split())
    except OSError:
        return set()


def proxy_host():
    return urlparse(config.PROXY_PUBLIC_URL).hostname or ""


def _bundle():
    """Evals/ + claude/ + .claude/ of the current release, plus the portal's guidelines/, packed under work/.
    Cached per release and guideline version."""
    with _bundle_lock:
        return _pack_bundle()


def _pack_bundle():
    base = Path(os.path.realpath(config.EVAL_BASE_DIR))
    # In-place edits (skill effort, refreshed hash pins) don't touch the top folders' mtimes, so include them.
    watched = [base / "Evals", base / "claude", *(base / "Evals" / "references").glob("*.json"),
               *(base / "claude" / "skills").glob("*/SKILL.md")]
    guides = sorted(config.GUIDELINES_DIR.glob("*.md"))  # portal-owned, so a guideline change is a portal deploy
    key = f"{base}|{max(int(p.stat().st_mtime_ns) for p in watched)}|" + hashlib.sha256(
        b"".join(g.name.encode() + g.read_bytes() for g in guides)).hexdigest()[:12]
    path = _bundle_cache.get(key)
    if path and path.is_file():
        return path
    out = config.DATA_DIR / "bundles"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"bundle-{hashlib.sha256(key.encode()).hexdigest()[:12]}.tgz"

    def skip(info):
        parts = Path(info.name).parts
        if any(p in ("task_inputs", ".cache", "__pycache__", "__MACOSX") for p in parts[2:]):
            return None
        if parts[-1] == ".DS_Store":
            return None
        return info

    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        for top in ("Evals", "claude", ".claude"):
            tar.add(str(base / top), f"work/{top}", filter=skip)
        for g in guides:
            tar.add(str(g), f"work/guidelines/{g.name}")
    tmp.rename(path)
    _bundle_cache[key] = path
    log.info("packed eval bundle %s (%.1f MB)", path.name, path.stat().st_size / 1e6)
    return path


_SESSION = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}(\.jsonl)?")


def _only_session(info, keep):
    """Upload filter for home/runner. Claude Code keeps every past session of the chat under
    .claude/projects/<project>/ (<id>.jsonl plus an <id>/ folder), up to 1.3 GB on long chats, and a review
    needs none of them but the one it resumes. Leaving the rest out cut a heavy upload from ~58 MB to ~12 MB
    while boat's network was slow (2026-10-29); restore_state merges home, so they stay on the server."""
    parts = info.name.split("/")  # home/runner/.claude/projects/<project>/<entry>/...
    if len(parts) >= 6 and parts[2:4] == [".claude", "projects"] and _SESSION.fullmatch(parts[5]):
        if parts[5].split(".")[0] != keep:
            return None
    return info


def build_payload(run_id, attempt_id, prompt, args, env, extra=None, scratch=None):
    """Write payload.tgz for one run and return its path. `extra`: more files for /tmp/payload.
    `scratch`: a paused review's /tmp scratch files (scratch.tgz), put back by RUN_SH."""
    root = workspace.attempt_dir(attempt_id)
    work = root / "logs" / f"{run_id}.payload"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    shutil.copy(_bundle(), work / "bundle.tgz")
    if scratch:
        shutil.copy(scratch, work / "scratch.tgz")
    resumed = args[args.index("--resume") + 1] if "--resume" in args[:-1] else None
    with tarfile.open(work / "state.tgz", "w:gz") as tar:
        tar.add(str(root / "task_inputs"), "work/Evals/task_inputs")
        tar.add(str(root / "cache"), "work/Evals/.cache")
        tar.add(str(root / "home"), "home/runner", filter=lambda info: _only_session(info, resumed))
    (work / "run.env").write_text("".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in env.items()))
    (work / "cmd.sh").write_text("#!/bin/bash\nset -a; . /tmp/payload/run.env; set +a\ncd /work\n"
                                 f"exec {shlex.join(args)} < /tmp/payload/prompt.txt\n")
    (work / "prompt.txt").write_text(prompt)
    for flag in ("--session-id", "--resume"):  # a Claude Code session can be resumed if the connection drops
        if flag in args[:-1]:
            at = args.index(flag)
            again = [*args[:at], "--resume", args[at + 1], *args[at + 2:]]
            (work / "resume.sh").write_text("#!/bin/bash\nset -a; . /tmp/payload/run.env; set +a\ncd /work\n"
                                            f"exec {shlex.join(again)} < /tmp/payload/resume_prompt.txt\n")
            (work / "resume_prompt.txt").write_text(RESUME_PROMPT)
            (work / "check.sh").write_text("#!/bin/bash\nset -a; . /tmp/payload/run.env; set +a\n"
                                           f"exec {shlex.quote(args[0])} --version\n")
            break
    (work / "review.sh").write_text(REVIEW_SH)
    (work / "run.sh").write_text(RUN_SH)
    (work / "collect.sh").write_text(COLLECT_SH)
    (work / "memguard.sh").write_text(MEMGUARD_SH)
    (work / "proxy_host").write_text(proxy_host() if config.BOAT_RESTRICT_EGRESS else "")
    for name, content in (extra or {}).items():
        (work / name).write_text(content)
    payload = root / "logs" / f"{run_id}.payload.tgz"
    with tarfile.open(payload, "w:gz") as tar:
        for item in sorted(work.iterdir()):
            tar.add(str(item), item.name)
    shutil.rmtree(work, ignore_errors=True)
    return payload


def restore_state(attempt_id, tgz):
    """Replace the attempt's task_inputs and cache with what the box sent back, and merge its home into the
    attempt's: the box only got the session it resumed (_only_session), so replacing home would delete the
    chat's other sessions, which follow-ups resume."""
    root = workspace.attempt_dir(attempt_id)
    tmp = root / f".restore-{int(time.time() * 1000)}"
    tmp.mkdir()
    try:
        with tarfile.open(tgz, "r:gz") as tar:
            tar.extractall(tmp, filter="data")  # the box is untrusted: no absolute paths, .. or outside links
        if (tmp / "home/runner").is_dir():
            shutil.copytree(tmp / "home/runner", root / "home", symlinks=True, dirs_exist_ok=True)
        for src, dst in (("work/Evals/task_inputs", "task_inputs"), ("work/Evals/.cache", "cache")):
            new = tmp / src
            if not new.is_dir():
                continue
            old = root / f"{dst}.old"
            shutil.rmtree(old, ignore_errors=True)
            if (root / dst).exists():
                (root / dst).rename(old)
            new.rename(root / dst)
            shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def kill(proc):
    """Kill a boat CLI process together with the ssh it started. Killing only boat leaves that ssh
    running, holding the output stream open, so a cancelled review kept going until it finished."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # boat is started in its own process group (start_new_session)
    except (ProcessLookupError, PermissionError):
        pass


async def cli(*args, timeout=300, input_bytes=None):
    """Run the boat CLI; return (returncode, stdout, stderr)."""
    # Global flags must come before a `--` remote command, or they become part of it.
    proc = await asyncio.create_subprocess_exec(
        config.BOAT_BIN, args[0], "--no-update", *args[1:], stdin=asyncio.subprocess.PIPE if input_bytes else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(input_bytes), timeout=timeout)
    except asyncio.TimeoutError:
        kill(proc)
        return -1, "", "timed out"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def create_box():
    """Clone a review box. If boat refuses before a box exists (e.g. the per-minute start limit when
    many reviews start at once), wait and retry instead of failing the review."""
    ttl = config.RUN_TIMEOUT_MIN * 60 + 1800  # auto-stop backstop if the server dies mid-run
    tries = 10  # waits add up to ~7.5 minutes: enough for 50 reviews started in the same minute
    for attempt in range(1, tries + 1):
        await _start_slot()
        rc, out, err = await cli("new", "--from", active_snapshot(), "--type", config.BOAT_BOX_TYPE,
                                 "--no-env", "--ttl", str(ttl), "--json", timeout=600)
        box, error = None, None
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            box = event.get("id") or box
            if event.get("event") == "error":
                error = event.get("error") or event
            elif event.get("event") == "ready" and box:
                return box
        if box or rc in (-1, -15) or attempt == tries:  # a box may exist (don't leak it), shutting down, or out of tries
            break
        wait = min(15 * attempt, 60)
        log.info("boat new refused (%s); retrying in %ss", str(error or err or out)[-200:], wait)
        await asyncio.sleep(wait)
    if box:  # boat made a machine but couldn't finish it (e.g. "readiness command did not succeed"): don't leak it
        await delete_box(box)
    if error:
        raise RuntimeError(f"boat new failed: {error}")
    raise RuntimeError(f"boat new did not report a ready box (rc={rc}): {(err or out)[-300:]}")


async def ssh(box, command, timeout=300):
    rc, out, err = await cli("ssh", box, "--", command, timeout=timeout)
    return rc, out


class MachineNotReady(RuntimeError):
    """The machine answered but can't run the review (Claude Code never became runnable): replace it, don't retry."""


async def retrying(what, step, tries=4):
    """A freshly started box can drop the first SSH connections; retry setup steps with a short backoff."""
    for attempt in range(1, tries + 1):
        try:
            return await step()
        except RuntimeError as error:
            if attempt == tries or isinstance(error, MachineNotReady):
                raise
            log.info("%s failed (try %d/%d), retrying: %s", what, attempt, tries, str(error)[-200:])
            await asyncio.sleep(5 * attempt)


async def scp_to(box, local, remote):
    rc, out, err = await cli("scp", str(local), f"{box}:{remote}", timeout=900)
    if rc != 0:
        raise RuntimeError(f"upload to {box} failed: {(err or out)[-300:]}")


async def scp_from(box, remote, local):
    rc, out, err = await cli("scp", f"{box}:{remote}", str(local), timeout=900)
    if rc != 0:
        raise RuntimeError(f"download from {box} failed: {(err or out)[-300:]}")


async def delete_box(box):
    rc, out, err = await cli("delete", box, "--yes", timeout=300)
    if rc != 0:
        log.warning("could not delete box %s: %s", box, (err or out)[-300:])


async def sweep_orphans(keep=()):
    """Delete review boxes a server restart left behind: every clone of the runner snapshot except the
    re-attached reviews in `keep`. Runs before the boat workers start, so it can't race a new review's
    box. The server and template boxes have their own names, and the server's own subdomain is skipped
    as well."""
    rc, out, err = await cli("list", "--all", "--json", timeout=120)
    try:
        boxes = json.loads(out).get("sandboxes", []) if rc == 0 else None
    except ValueError:
        boxes = None
    if boxes is None:
        log.warning("could not list boxes to clean up: %s", (err or out)[-300:])
        return
    ours = f"{config.PUBLIC_BASE_URL} {config.PROXY_PUBLIC_URL}"
    keep = set(keep) | _kept_boxes()  # plus boxes an eval-files update is building right now
    for box in boxes:
        if box.get("id") in keep:
            continue
        # clones are named after their image: hg-runner, or hg-runner-<stamp> after an update
        if (box.get("name") or "").startswith(config.BOAT_SNAPSHOT) and not (box.get("subdomain") and box["subdomain"] in ours):
            log.info("deleting leftover review box %s (created %s)", box["id"], box.get("createdAt"))
            await delete_box(box["id"])


# Load-test stand-in for Claude Code (runs as `runner`): the same stream-json shape, a model call through
# the metering proxy every 15 s, then a report. The prompt file holds the duration in seconds.
FAKE_REVIEW_SH = r"""#!/bin/bash
read -r DUR || DUR=300
echo '{"type":"system","subtype":"init","session_id":"loadtest"}'
for i in $(seq 1 $(( DUR / 15 ))); do
  sleep 15
  code=$(curl -s -o /dev/null -m 120 -w '%{http_code}' -X POST "$ANTHROPIC_BASE_URL/v1/messages" \
    -H "authorization: Bearer $ANTHROPIC_AUTH_TOKEN" -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
    -d '{"model":"claude-opus-5-5","max_tokens":8,"messages":[{"role":"user","content":"ping"}]}')
  echo "{\"type\":\"assistant\",\"message\":{\"id\":\"msg_$i\",\"content\":[{\"type\":\"tool_use\",\"name\":\"Bash\",\"input\":{\"command\":\"load-test step $i: proxy $code\"}}]}}"
done
mkdir -p /work/Evals/task_inputs/reports
cp /tmp/payload/fake_report.md /work/Evals/task_inputs/reports/0_injection.md
echo '{"type":"result","subtype":"success","is_error":false,"result":"Load-test review finished.","total_cost_usd":0,"num_turns":1}'
"""
