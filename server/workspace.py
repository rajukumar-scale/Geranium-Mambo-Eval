"""Per-attempt state on disk, the runner workspace layout, and report parsing.

Each attempt owns DATA_DIR/attempts/<id>/ with:
  task_inputs/   saved inputs + reports (what the eval calls Evals/task_inputs)
  cache/         the eval's Evals/.cache for this attempt
  home/          HOME for the runner (Claude Code sessions live in home/.claude)
  logs/          raw stream-json of every run

docker mode mounts the shared eval bundle read-only at /work and these folders
on top, so the eval rules and tools can't be edited by a run. local mode (dev)
swaps them into one hardlinked workspace instead.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

from . import config, skill_effort

STAGE_BY_FILE = {
    "0_injection.md": "injection",
    "1_prompt.md": "prompt",
    "1a_hardness.md": "hardness",
    "2_oe.md": "oe",
    "3_rubric.md": "rubric",
}
LOCAL_WS = config.DATA_DIR / "local_ws"
_swapped_in = {}  # attempt_id -> True while its task_inputs live inside LOCAL_WS
_parser = None


def attempt_dir(attempt_id):
    return config.DATA_DIR / "attempts" / attempt_id


def ensure_attempt(attempt_id, unpack=True):
    """The attempt's folders. unpack=False for paths that never read home/ or cache/ (sign-in, writing a message's
    prompt): unpacking a cold chat takes seconds, and those paths run on the event loop. Runs unpack in a thread first
    (Runner._execute), so the unpack here is the backstop for anything else that uses home/ or cache/."""
    root = attempt_dir(attempt_id)
    if unpack:
        thaw(attempt_id)
    for sub in ("task_inputs/reports", "cache", "home/.claude", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


# ---- cold storage ---------------------------------------------------------------
# An idle chat's home/ (Claude Code sessions, ~6.6 GB a day across chats on 2026-10-31) and cache/ are packed into
# one cold.tgz (text, ~8x smaller) by housekeeping, and unpacked by ensure_attempt() before anything uses them, so a
# chat picked up again weeks later resumes exactly as before. task_inputs/ and logs/ stay as they are (the pages read them).
COLD = "cold.tgz"
COLD_DIRS = ("home", "cache")
_cold_locks = {}
_cold_guard = threading.Lock()


def _cold_lock(attempt_id):
    with _cold_guard:
        return _cold_locks.setdefault(attempt_id, threading.Lock())


def is_cold(attempt_id):
    """Packed here (cold.tgz) or moved to S3 (archive.py: archived.json)."""
    root = attempt_dir(attempt_id)
    return (root / COLD).is_file() or (root / "archived.json").is_file()


def freeze(attempt_id, busy=lambda: False):
    """Pack home/ and cache/ into cold.tgz and remove them. busy() is checked under the lock, so a run starting at the
    same moment either waits for the pack to finish (and unpacks it) or keeps it from starting. Returns bytes freed."""
    root = attempt_dir(attempt_id)
    with _cold_lock(attempt_id):
        dirs = [d for d in COLD_DIRS if (root / d).is_dir()]
        if (root / COLD).exists() or (root / "archived.json").exists() or not dirs or busy():
            return 0  # archived: its sessions come back from S3 on thaw; empty dirs made meanwhile mustn't replace them
        before = sum(f.lstat().st_size for d in dirs for f in (root / d).rglob("*") if f.is_file() or f.is_symlink())
        tmp = root / (COLD + ".tmp")
        try:
            with tarfile.open(tmp, "w:gz", compresslevel=6) as tar:
                for d in dirs:
                    tar.add(str(root / d), d)
            tmp.replace(root / COLD)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        for d in dirs:
            shutil.rmtree(root / d)
        return before - (root / COLD).stat().st_size


def thaw(attempt_id):
    """Unpack cold.tgz back into home/ and cache/ (a no-op for a chat that isn't cold). An archived chat's cold.tgz is
    first downloaded back from S3 (archive.fetch)."""
    root = attempt_dir(attempt_id)
    if not (root / COLD).is_file() and not (root / "archived.json").is_file():
        return False
    with _cold_lock(attempt_id):
        if (root / "archived.json").is_file() and not (root / COLD).is_file():
            from . import archive
            archive.fetch(attempt_id)
        packed = root / COLD
        if not packed.is_file():
            return False
        with tarfile.open(packed, "r:gz") as tar:
            tar.extractall(root, filter="tar")  # our own archive; "tar" also keeps symlinks Claude Code may leave
        packed.unlink()
        return True


def task_inputs(attempt_id):
    """Where this attempt's task_inputs currently are (moves in local mode during a run)."""
    if _swapped_in.get(attempt_id):
        return LOCAL_WS / "Evals" / "task_inputs"
    return attempt_dir(attempt_id) / "task_inputs"


def uploads_dir(attempt_id):
    path = task_inputs(attempt_id) / "_uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name):
    name = os.path.basename(name or "file").strip().replace("\x00", "")
    name = re.sub(r"[^A-Za-z0-9._ ()\-]+", "_", name)[:120].strip(" .") or "file"
    return name


# ---- reports -----------------------------------------------------------------

def _report_parser():
    """The bundle's own verdict parser, loaded from the trusted read-only base copy. It imports its
    sibling tools on demand (hg_rubric_loop for rubric reports, hg_golden_review for OE ones), so their
    folder must be importable. Reloaded when an eval-files update points the base at a new release."""
    global _parser
    tools = (config.EVAL_BASE_DIR / "Evals" / "tools").resolve()
    if _parser is None or _parser[0] != tools:
        if _parser is not None:  # drop the previous release's tools
            old = str(_parser[0])
            if old in sys.path:
                sys.path.remove(old)
            for name, module in list(sys.modules.items()):
                if (getattr(module, "__file__", None) or "").startswith(old + os.sep):
                    del sys.modules[name]
        sys.path.append(str(tools))  # appended, so the bundle's hg_* names can't shadow the server's imports
        spec = importlib.util.spec_from_file_location("hg_report_contract_base", tools / "hg_report_contract.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _parser = (tools, module)
    return _parser[1]


HASH_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9 /()-]*?SHA-256):\s*([0-9a-f]{64})\b")
TEXT_FIELDS = ("Eval version", "Evaluator model", "Review policy", "Review date")


def parse_report(stage, text):
    info = {"stage": stage, "sha256": hashlib.sha256(text.encode()).hexdigest(), "hashes": {}, "fields": {}}
    try:
        result = _report_parser().parse_result(stage, text)
        info.update(verdict=result.get("verdict"), score=result.get("verdict_score"),
                    exec_status=result.get("execution_status"), parsed=result)
    except Exception as error:  # noqa: BLE001 - report text is untrusted input
        info.update(verdict=None, score=None, exec_status=None, parse_error=str(error)[:300])
    for raw in text.splitlines():
        line = raw.replace("**", "").strip()
        m = HASH_LINE.match(line)
        if m and m.group(1) not in info["hashes"]:
            info["hashes"][m.group(1)] = m.group(2)
        for field in TEXT_FIELDS:
            if line.startswith(field + ":") and field not in info["fields"]:
                info["fields"][field] = line.split(":", 1)[1].strip()[:200]
    return info


def report_snapshot(attempt_id):
    """{file name: mtime} for every report present."""
    reports = task_inputs(attempt_id) / "reports"
    out = {}
    for name in STAGE_BY_FILE:
        path = reports / name
        if path.is_file():
            out[name] = path.stat().st_mtime
    return out


def read_report(attempt_id, name):
    path = task_inputs(attempt_id) / "reports" / name
    return path.read_text(encoding="utf-8-sig") if path.is_file() else None


def list_reports(attempt_id):
    out = []
    for name, mtime in sorted(report_snapshot(attempt_id).items()):
        text = read_report(attempt_id, name) or ""
        info = parse_report(STAGE_BY_FILE[name], text)
        info.pop("parsed", None)
        out.append({"file": name, "mtime": mtime, **info})
    return out


def saved_inputs(attempt_id):
    path = task_inputs(attempt_id) / "inputs.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for key, entry in (data.get("inputs") or {}).items():
        files = entry.get("files") or []
        out.append({"key": key, "state": entry.get("state"), "revision": entry.get("revision"),
                    "files": [{"path": f.get("path"), "bytes": f.get("bytes"), "sha256": f.get("sha256")} for f in files]})
    return out


# The task date (since the 2026-10-20 eval): each task is on one of two universes, picked by its saved date.
UNIVERSE_DATES = {"normal": ("2026-02-14", "Feb 14, 2026 (normal)"), "synthcut": ("2025-10-01", "Oct 1, 2025 (synth-cut)")}


def dated_release(base=None):
    """Does the installed eval ask for a task date (two universes)?"""
    return ((base or config.EVAL_BASE_DIR) / "Evals" / "tools" / "hg_universe.py").is_file()


def universe_date(attempt_id):
    """The task date this attempt saved ("normal" / "synthcut"), or None. Checked like the eval does: the saved
    file must exist and match its recorded hash, or the eval asks again."""
    entry = next((i for i in saved_inputs(attempt_id) if i["key"] == "universe"), None)
    if not entry or entry["state"] != "saved" or len(entry["files"]) != 1:
        return None
    root = task_inputs(attempt_id).resolve()
    path = _input_path(root, entry["files"][0])
    try:
        raw = path.read_bytes() if path else b""
    except OSError:
        return None
    if not raw or hashlib.sha256(raw).hexdigest() != entry["files"][0].get("sha256"):
        return None
    date = raw.decode("utf-8", "replace").strip()
    return next((key for key, (d, _) in UNIVERSE_DATES.items() if d == date), None)


def _input_path(root, f):
    """Where a file listed in inputs.json is, or None if it's gone or points outside task_inputs."""
    path = (root / (f.get("path") or "").removeprefix("Evals/task_inputs/")).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def read_input(attempt_id, key, limit=15_000_000):
    """The saved files of one input, for the user to check what was stored. Only files listed in the
    attempt's inputs.json, and only inside its task_inputs folder. `limit` caps the text shown: 15 MB fits
    every saved input, Opus trajectories included (largest 8.8 MB on 2026-10-17; it opens in about 3 s).
    input_file() gives the whole file."""
    entry = next((i for i in saved_inputs(attempt_id) if i["key"] == key), None)
    if not entry:
        return None
    root = task_inputs(attempt_id).resolve()
    out = []
    for f in entry["files"]:
        path = _input_path(root, f)
        if not path:
            out.append({"path": f.get("path"), "bytes": f.get("bytes"), "missing": True})
            continue
        data = path.read_bytes()[:limit]
        binary = b"\0" in data[:4096]
        out.append({"path": f.get("path"), "bytes": path.stat().st_size, "binary": binary,
                    "truncated": path.stat().st_size > limit,
                    "text": None if binary else data.decode("utf-8", errors="replace")})
    return {"key": key, "state": entry["state"], "revision": entry["revision"], "files": out}


def input_file(attempt_id, key, index=0):
    """The path of one whole saved file of an input (for Download), or None."""
    entry = next((i for i in saved_inputs(attempt_id) if i["key"] == key), None)
    if not entry or not 0 <= index < len(entry["files"]):
        return None
    return _input_path(task_inputs(attempt_id).resolve(), entry["files"][index])


# ---- local (development) workspace -----------------------------------------------

def _hardlink_tree(src, dst):
    for dirpath, dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target = dst / rel if rel != "." else dst
        target.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            out = target / name
            if not out.exists():
                os.link(os.path.join(dirpath, name), out)


def prepare_local_workspace(log=print):
    """Refresh LOCAL_WS from the base bundle. Universes are hardlinked once (no extra disk)."""
    base = config.EVAL_BASE_DIR
    LOCAL_WS.mkdir(parents=True, exist_ok=True)
    rsync = ["rsync", "-a", "--delete", "--exclude", "task_inputs/", "--exclude", ".cache/",
             "--exclude", "__pycache__/", "--exclude", ".DS_Store"]
    subprocess.run(rsync + [f"{base}/Evals/", f"{LOCAL_WS}/Evals/"], check=True)
    subprocess.run(rsync + [f"{base}/claude/", f"{LOCAL_WS}/claude/"], check=True)
    subprocess.run(rsync + ["--include", "*.md", "--exclude", "*", f"{config.GUIDELINES_DIR}/", f"{LOCAL_WS}/guidelines/"],
                   check=True)
    skill_effort.apply(LOCAL_WS / "claude" / "skills", config.EVAL_EFFORT)
    subprocess.run(["python3", str(LOCAL_WS / "claude" / "install.py")], check=True, capture_output=True)
    for name in config.universe_dirs(base):
        marker = LOCAL_WS / f".{name}.linked"
        if not marker.exists():
            log(f"hardlinking {name} into the local workspace (one-time)...")
            started = time.time()
            _hardlink_tree(base / name, LOCAL_WS / name)
            marker.write_text(str(time.time()))
            log(f"  done in {time.time() - started:.0f}s")


def swap_in(attempt_id):
    """Move this attempt's state into LOCAL_WS. Single-slot: callers hold the run lock."""
    ws_inputs, ws_cache = LOCAL_WS / "Evals" / "task_inputs", LOCAL_WS / "Evals" / ".cache"
    _park_leftovers()
    root = ensure_attempt(attempt_id)
    shutil.move(str(root / "task_inputs"), ws_inputs)
    shutil.move(str(root / "cache"), ws_cache)
    (LOCAL_WS / ".owner").write_text(attempt_id)
    _swapped_in[attempt_id] = True
    return LOCAL_WS


def swap_out(attempt_id):
    root = attempt_dir(attempt_id)
    ws_inputs, ws_cache = LOCAL_WS / "Evals" / "task_inputs", LOCAL_WS / "Evals" / ".cache"
    if ws_inputs.exists():
        shutil.move(str(ws_inputs), root / "task_inputs")
    if ws_cache.exists():
        shutil.move(str(ws_cache), root / "cache")
    (LOCAL_WS / ".owner").unlink(missing_ok=True)
    _swapped_in.pop(attempt_id, None)


def _park_leftovers():
    """After a crash, give a leftover workspace state back to its owner."""
    owner_file = LOCAL_WS / ".owner"
    if owner_file.exists():
        swap_out(owner_file.read_text().strip())
    for leftover in (LOCAL_WS / "Evals" / "task_inputs", LOCAL_WS / "Evals" / ".cache"):
        if leftover.exists():
            leftover.rename(leftover.with_name(f"{leftover.name}.orphan-{int(time.time())}"))


def recover_local():
    if LOCAL_WS.exists():
        _park_leftovers()


# ---- docker mounts -------------------------------------------------------------

def docker_mounts(attempt_id):
    base, root = config.EVAL_BASE_DIR, ensure_attempt(attempt_id)
    mounts = [
        (base / "Evals", "/work/Evals", "ro"),
        (root / "task_inputs", "/work/Evals/task_inputs", "rw"),
        (root / "cache", "/work/Evals/.cache", "rw"),
        (base / "claude", "/work/claude", "ro"),
        (base / ".claude", "/work/.claude", "ro"),
        (root / "home", "/home/runner", "rw"),
        (config.GUIDELINES_DIR, "/work/guidelines", "ro"),
    ]
    mounts += [(base / name, f"/work/{name}", "ro") for name in config.universe_dirs(base)]
    args = []
    for src, dst, mode in mounts:
        args += ["--mount", f"type=bind,source={src},target={dst}" + (",readonly" if mode == "ro" else "")]
    return args
