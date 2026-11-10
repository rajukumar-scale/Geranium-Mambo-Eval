"""Portal settings, read once from the environment (and an optional .env file)."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"'):
            value = value[1:].split(value[0], 1)[0]
        else:  # drop an inline comment: VALUE   # note, or KEY=   # note (empty)
            value = re.split(r"(?:^|\s+)#", value, maxsplit=1)[0].strip()
        # The portal's own .env wins over inherited shell variables of the same name.
        os.environ[key.strip()] = value


_load_dotenv(Path(os.environ.get("PORTAL_ENV_FILE", ROOT / ".env")))


def _get(name, default=None):
    value = os.environ.get(name)
    return default if value in (None, "") else value


def _int(name, default):
    return int(_get(name, default))


def _list(name, default=""):
    return [x.strip() for x in str(_get(name, default)).split(",") if x.strip()]


DATA_DIR = Path(_get("PORTAL_DATA_DIR", ROOT / "data")).resolve()
# Folder holding Evals/, claude/, .claude/ (from install.py) and the universes: since 2026-10-20 two of them,
# Universe_Normal/ (Feb 14, 2026) and Universe_SynthCut/ (Oct 1, 2025), each with the three HarmonyGames_* exports;
# before that the three HarmonyGames_* folders directly (UNIVERSE_DIRS, still used for an older release).
# Not resolved on purpose: on the VM this is a symlink to the current bundle version,
# so updating the eval swaps it for new runs without touching runs in progress.
EVAL_BASE_DIR = Path(os.path.abspath(_get("EVAL_BASE_DIR", ROOT.parent / "New_Evals")))
# Contributor guidelines the chat assistant reads (hardness, tool-call minimum). Portal-owned, not part of the eval
# release: every review workspace gets them at work/guidelines (boat bundle, docker mount, local workspace).
GUIDELINES_DIR = ROOT / "server" / "guidelines"
# Portal-owned text for the "plan your task" kickoff (server/plan.py): the card, the hidden note behind its button and
# the early guard appended to the assistant's rules. Read by the portal itself; not copied into review workspaces.
PROMPTS_DIR = ROOT / "server" / "prompts"
UNIVERSE_DIRS = _list("UNIVERSE_DIRS", "HarmonyGames_GitHub,HarmonyGames_Gmail,HarmonyGames_Other_Services")
DATED_UNIVERSES = ("Universe_Normal", "Universe_SynthCut")


def universe_dirs(base=None):
    """The universe folders of the installed release (mounted at /work/<name>): the two dated universes when it
    has them, else the old three folders."""
    base = Path(base or EVAL_BASE_DIR)
    dated = [name for name in DATED_UNIVERSES if (base / name).is_dir()]
    return dated or UNIVERSE_DIRS

# docker = one container per run (VM). local = runs on this machine in a single
# hardlinked workspace (development only, no isolation).
RUNNER_MODE = _get("RUNNER_MODE", "local")
RUNNER_IMAGE = _get("RUNNER_IMAGE", "eval-runner:latest")
# The dataset-similarity runtime (deploy/setup_similarity.sh) on this machine, mounted read-only into docker reviews.
# Boat review boxes have it baked in; the docker image (runner/Dockerfile) doesn't, so without this mount every /prompt
# run in docker ended PARTIAL "dataset similarity runtime is missing" (eval-portal.scale.com, 2026-11-07).
SIMCHECK_DIR = Path(_get("SIMCHECK_DIR", "/opt/simcheck"))
CONTAINER_MEMORY = _get("CONTAINER_MEMORY", "4g")
CONTAINER_CPUS = _get("CONTAINER_CPUS", "2")
# An --internal docker network (no internet) whose gateway is where the proxy listens. setup_vm.sh creates it.
DOCKER_NETWORK = _get("DOCKER_NETWORK", "bridge")
CLAUDE_BIN = _get("CLAUDE_BIN", "claude")

LITELLM_BASE_URL = _get("LITELLM_BASE_URL", "https://litellm-proxy.ml.scale.com").rstrip("/")
LITELLM_API_KEY = _get("LITELLM_API_KEY", "")
# The taxonomy's pass-screen reader (server/taxo_reader.py): the same model and LiteLLM endpoint the taxonomy uses
# to read uploaded pass screens. The portal reads its own pass image with it before the contributor downloads it.
READER_URL = _get("READER_URL", "https://litellm-proxy.ml-serving-internal.scale.com/v1/chat/completions")
READER_API_KEY = _get("READER_API_KEY", LITELLM_API_KEY)
# A backup LiteLLM key, used only while LITELLM_API_KEY answers "budget exceeded" (proxy.py, taxo_reader.py), then back
# to the main key once it works again (Evan, 2026-11-01). Empty = no fallback; the main key's errors reach the reviews.
LITELLM_FALLBACK_API_KEY = _get("LITELLM_FALLBACK_API_KEY", "")
LITELLM_FALLBACK_ALIAS = _get("LITELLM_FALLBACK_ALIAS", "the backup key")
READER_MODEL = _get("READER_MODEL", "gpt-4o")
READER_CHECKS_PER_HOUR = _int("READER_CHECKS_PER_HOUR", 60)  # per attempt; one download is 1-3 reads (~$0.005 each)

REDASH_URL = _get("REDASH_URL", "https://redash.scale.com").rstrip("/")
REDASH_API_KEY = _get("REDASH_API_KEY", "")
REDASH_LOGIN_QUERY_ID = _int("REDASH_LOGIN_QUERY_ID", 387117)
# The platform task record (persona, business function, competency, skill, task type, difficulty mode, universe) for
# server/assignment.py, which fills a chat's assignment and task date from it (2026-11-03). 0 turns that off.
REDASH_TASK_QUERY_ID = _int("REDASH_TASK_QUERY_ID", 399176)
ASSIGNMENT_AUTOFILL = _int("ASSIGNMENT_AUTOFILL", 1)
# Budget of the LiteLLM key, for the admin page: a saved query on the LiteLLM tables (0 = no budget panel).
BUDGET_QUERY_ID = _int("BUDGET_QUERY_ID", 388350)
BUDGET_DAILY_QUERY_ID = _int("BUDGET_DAILY_QUERY_ID", 389171)  # the key's spend per UTC day, for the bar chart (0 = none)
# The same for the backup key the meter falls back to (LITELLM_FALLBACK_API_KEY): 388350 with the alias swapped (0 = none).
BACKUP_BUDGET_QUERY_ID = _int("BACKUP_BUDGET_QUERY_ID", 397844)
# The admin Cost page: one saved Redash query with every fact since 2026-08-01 (per day, per task x contributor x day,
# each submission), refreshed in the background; the page cuts any date range from it. v4 (2026-10-28) replaces the
# per-range queries 390879 / 390881 / 390882, with the same rules (Atlas Commercial spend on every key, LiteLLM + SMIG).
# v5 (2026-11-09) = 401890: the same rows as v4 plus the pipeline layer of each (column lvl), per-day x layer rows (dl)
# and each node's layer (node); 395415 (v4) stays as it was, for a rollback.
# v6 (2026-11-10) = 402024: v5 + kind tr, everything spent on each task by anyone (runs tied by the task id they carry).
COST_FACTS_QUERY_ID = _int("COST_FACTS_QUERY_ID", 402024)
# SMIG's billed cost per portal run (runId tag, sent since 2026-10-26 20:00 UTC); untagged runs keep the list-price estimate (0 = estimates only).
COST_PORTAL_SMIG_QUERY_ID = _int("COST_PORTAL_SMIG_QUERY_ID", 394171)
# Each Atlas Commercial task's platform state (ever submitted at L-1/L0, cancelled, claimed, paused, last activity), for the
# Cost page's lost spend: tasks never submitted and quiet for 3 days (0 = the page leaves lost spend out).
COST_TASK_STATES_QUERY_ID = _int("COST_TASK_STATES_QUERY_ID", 396595)
# The admin CB tracker (/admin/cbs): Atlas Commercial L-1 nodes, submits, incomplete attempts and each person's worker
# source and mcp_ac_trusted days, refreshed every 30 minutes (0 = the page is off).
CB_FACTS_QUERY_ID = _int("CB_FACTS_QUERY_ID", 397453)
BUDGET_KEY_ALIAS = _get("BUDGET_KEY_ALIAS", "pub-atlas-commercial-sandbox")
# Platform submissions for tasks passed at /rubric, for the admin "Where they are now" panel (0 = don't hide anyone).
SUBMISSIONS_QUERY_ID = _int("SUBMISSIONS_QUERY_ID", 388752)
# Who holds each task on the platform, for the "done, not submitted" debugger (0 = portal data only).
CLAIMS_QUERY_ID = _int("CLAIMS_QUERY_ID", 388856)
# Empty list = any project.
ALLOWED_PROJECT_IDS = _list("ALLOWED_PROJECT_IDS", "6a5595596cf3031de47f0ea2")

SESSION_SECRET = _get("SESSION_SECRET", "")
PROOF_SECRET = _get("PROOF_SECRET", "")
COOKIE_SECURE = _get("COOKIE_SECURE", "0") == "1"
SESSION_DAYS = _int("SESSION_DAYS", 7)

# Owner logins: a plain username (e.g. atlasadmin) or an email. All of them use ADMIN_PASSWORD. Only owners see the Cost page.
ADMIN_EMAILS = [e.lower() for e in _list("ADMIN_EMAILS", "atlasadmin")]
ADMIN_PASSWORD = _get("ADMIN_PASSWORD", "")

SLACK_WEBHOOK_URL = _get("SLACK_WEBHOOK_URL", "")
# 0 = alerts carry no contributor details (safe for a channel contributors can read); 1 = email + message too.
SLACK_ALERT_DETAIL = _int("SLACK_ALERT_DETAIL", 0)
SLACK_BOT_TOKEN = _get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = _get("SLACK_CHANNEL", "")
# A Slack workflow webhook whose workflow DMs Evan (2026-11-06: there is no bot token). The DM route for the
# linter-stuck and outdated-pass-submit alerts when SLACK_BOT_TOKEN is unset; posts {"text": ...} like SLACK_WEBHOOK_URL.
SLACK_DM_WEBHOOK_URL = _get("SLACK_DM_WEBHOOK_URL", "")
# Slack DM to Evan when a CB is stuck on a fingerprint linter (server/linter_alerts.py): SLACK_BOT_TOKEN or SLACK_DM_WEBHOOK_URL.
LINTER_ALERT_QUERY_ID = _int("LINTER_ALERT_QUERY_ID", 395304)  # 0 = off
LINTER_ALERT_SLACK_USER = _get("LINTER_ALERT_SLACK_USER", "U0BD6FV1PJ4")
LINTER_ALERT_EVERY_MIN = _int("LINTER_ALERT_EVERY_MIN", 15)
LINTER_ALERT_REPEAT_HOURS = _int("LINTER_ALERT_REPEAT_HOURS", 3)  # same CB/task/stage again only after this
# DM Evan when a task is submitted while one of its portal passes is outdated (server/submit_watch.py). 0 = off.
SUBMIT_WATCH = _int("SUBMIT_WATCH", 1)
LINTER_ALERT_DASHBOARD_URL = _get("LINTER_ALERT_DASHBOARD_URL",
                                  "https://redash.scale.com/dashboards/12117-mcp-ac-fingerprint-linter-monitor")
PUBLIC_BASE_URL = _get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# Set to 1 behind Caddy/nginx so login rate limits use the real client IP.
TRUST_PROXY = _get("TRUST_PROXY", "0") == "1"
HOST = _get("HOST", "127.0.0.1")
PORT = _int("PORT", 8000)
# The metering proxy the eval runner talks to instead of LiteLLM. In docker
# mode it must listen on the docker bridge gateway (usually 172.17.0.1).
PROXY_HOST = _get("PROXY_HOST", "127.0.0.1")
PROXY_PORT = _int("PROXY_PORT", 8001)
# Base URL as seen from inside the runner (container or local process).
PROXY_URL_FOR_RUNNER = _get(
    "PROXY_URL_FOR_RUNNER",
    f"http://host.docker.internal:{PROXY_PORT}" if RUNNER_MODE == "docker" else f"http://127.0.0.1:{PROXY_PORT}",
)

MAX_CONCURRENT_RUNS = _int("MAX_CONCURRENT_RUNS", 4 if RUNNER_MODE == "docker" else 1)
# REVIEW_RUNNER=boat: every review (/injection /prompt /hardness /oe /rubric) gets its own fresh boat.dev box,
# cloned from BOAT_SNAPSHOT; follow-ups stay on RUNNER_MODE. Empty = reviews also use RUNNER_MODE.
REVIEW_RUNNER = _get("REVIEW_RUNNER", "")
BOAT_BIN = _get("BOAT_BIN", "boat")
# The portal's own boat box (server/selfguard.py keeps its auto-stop off). BOAT_ID is set in boat shells only.
SERVER_BOX_ID = _get("SERVER_BOX_ID", "") or os.environ.get("BOAT_ID", "") or "bx_qnsgbtzk"
BOAT_SNAPSHOT = _get("BOAT_SNAPSHOT", "hg-runner")
BOAT_BOX_TYPE = _get("BOAT_BOX_TYPE", "default")
MAX_BOAT_RUNS = _int("MAX_BOAT_RUNS", 20)
# Server first, boat as overflow (2026-10-30): boat bills machine hours, and this server is paid for around the clock
# and mostly idle. With REVIEW_RUNNER=boat and RUNNER_MODE=docker, these commands run in a container here while a
# server slot is free and at least SERVER_REVIEW_MIN_FREE_GB of memory is available; otherwise on a boat box as before,
# so they never queue. Their own slots: follow-ups (MAX_CONCURRENT_RUNS) never wait for them. 0 slots = off.
SERVER_REVIEW_SLOTS = _int("SERVER_REVIEW_SLOTS", 5)
SERVER_REVIEW_COMMANDS = _list("SERVER_REVIEW_COMMANDS", "injection,hardness,oe")
SERVER_REVIEW_MIN_FREE_GB = float(_get("SERVER_REVIEW_MIN_FREE_GB", 5))
# Public URL of the metering proxy, as reached from review boxes (e.g. https://<id>-8001.on.boat.dev).
PROXY_PUBLIC_URL = _get("PROXY_PUBLIC_URL", "").rstrip("/")
BOAT_RESTRICT_EGRESS = _get("BOAT_RESTRICT_EGRESS", "1") == "1"
MODAL_PROXY_URL = _get("MODAL_PROXY_URL", "https://modal-proxy.internal.scale.com").rstrip("/")
MODAL_AWS_PROFILE = _get("MODAL_AWS_PROFILE", "")
MODAL_BASE_IMAGE = _get("MODAL_BASE_IMAGE", "ubuntu:24.04")
MODAL_TEMPLATE_IMAGE = _get("MODAL_TEMPLATE_IMAGE", "")
MODAL_PROJECT_ID = _get("MODAL_PROJECT_ID", "6a5595596cf3031de47f0ea2")
MODAL_CPU = float(_get("MODAL_CPU", "4"))
MODAL_MEMORY_MB = _int("MODAL_MEMORY_MB", 8192)
# Usage caps are off by default (0 = unlimited); set them only if spend needs a ceiling.
DAILY_REVIEW_LIMIT = _int("DAILY_REVIEW_LIMIT", 0)
DAILY_FOLLOWUP_LIMIT = _int("DAILY_FOLLOWUP_LIMIT", 0)
RUN_TIMEOUT_MIN = _int("RUN_TIMEOUT_MIN", 150)  # /rubric with its fix rechecks ran past 90 min (2026-10-30)
# Prompt-cache TTL for chat replies and answers to a paused review (runner._cache_ttl, 2026-11-03): "1h" (default),
# "5m", or empty for Claude Code's default. Reviews keep the 5-minute default.
CHAT_PROMPT_CACHE_TTL = _get("CHAT_PROMPT_CACHE_TTL", "1h").strip()
MAX_CALLS_PER_RUN = _int("MAX_CALLS_PER_RUN", 0)
# Spend limit per contributor (USD, all their attempts, all time). Shown in their top bar; reaching it flags them,
# warns them in the chat and alerts the admins. Runs are not blocked. 0 = no limit shown.
CB_SPEND_LIMIT_USD = float(_get("CB_SPEND_LIMIT_USD", 400))  # per attempt; past it, runs need an admin's approval (2026-11-03)
# boat.dev start limit per minute ($20 plan 12, $100 plan 30): new review boxes are requested no faster than this.
BOAT_STARTS_PER_MIN = _int("BOAT_STARTS_PER_MIN", 12)
# Reused review boxes (boxpool.py): a box that ended a review cleanly is wiped and reused, up to BOX_REUSE_MAX reviews
# (0 = a new box per review, as before) and BOX_MAX_AGE_H hours; an idle one is deleted after BOX_IDLE_MIN minutes.
BOX_REUSE_MAX = _int("BOX_REUSE_MAX", 15)
BOX_MAX_AGE_H = float(_get("BOX_MAX_AGE_H", "8"))
BOX_IDLE_MIN = float(_get("BOX_IDLE_MIN", "5"))
# Warm boxes (boxpool.py, 2026-11-02): while reviews are coming in (one started on boat in the last BOX_WARM_BUSY_MIN
# minutes), keep at least BOX_WARM_MIN clean boxes started and waiting, so a review doesn't wait on boat's boot (or its
# failed boots). 0 = off. Idle warm boxes cost VM time: ~$0.036 an hour each.
BOX_WARM_MIN = _int("BOX_WARM_MIN", 6)  # 3 until 2026-11-02 evening: every review runs on boat now (no server slots)
BOX_WARM_BUSY_MIN = float(_get("BOX_WARM_BUSY_MIN", "20"))
# Slack alert when the boat wallet drops under this many dollars or boat can't start machines (boat_wallet.py; 0 = off).
BOAT_WALLET_ALERT_USD = float(_get("BOAT_WALLET_ALERT_USD", "20"))
# Where the eval zips live (the admin page's "Eval files" panel updates from these links).
EVAL_ZIP_URL = _get("EVAL_ZIP_URL", "https://static.remotasks.com/uploads/6a5595596cf3031de47f0ea2").rstrip("/")
# The zips, by name; a universe zip "<Normal|SynthCut>_HarmonyGames_X.zip" unpacks to Universe_<...>/<same name>.
# Normal's GitHub is three parts that unpack into one folder (update any part = all three).
EVAL_FILES = ("Evals", "claude", *(f"Normal_HarmonyGames_GitHub_part{i}of3" for i in (1, 2, 3)),
              "Normal_HarmonyGames_Gmail", "Normal_HarmonyGames_Other_Services",
              "SynthCut_HarmonyGames_GitHub", "SynthCut_HarmonyGames_Gmail", "SynthCut_HarmonyGames_Other_Services")
# Owner-only load test (/api/admin/loadtest): stand-in reviews on real boxes, canned model replies, no model spend.
LOADTEST_ALLOWED = _int("LOADTEST_ALLOWED", 0)
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 150)  # trajectory JSONs for /hardness can be large
CACHE_TTL_DAYS = _int("CACHE_TTL_DAYS", 5)
# Admin page: a chat whose holder still has the task but hasn't worked on it for this long shows as idle.
IDLE_DAYS = float(_get("IDLE_DAYS", 2))
# Status page (/status): a review start slower than this many minutes is "degraded"; failure shares (percent) of
# reviews or model calls in an hour above YELLOW are degraded and above RED a major outage.
STATUS_SLOW_START_MIN = float(_get("STATUS_SLOW_START_MIN", 10))
STATUS_YELLOW_PCT = float(_get("STATUS_YELLOW_PCT", 10))
STATUS_RED_PCT = float(_get("STATUS_RED_PCT", 30))
# A chat counts as released (no claim, no submission) only after this long without work: claim data lags a little.
RELEASE_GRACE_HOURS = float(_get("RELEASE_GRACE_HOURS", 2))
# Who sees the chat page's "Known data issues" sidebar (server/known_issues.py): emails or Outlier IDs, matched to
# the attempt's owner or the admin login. "*" = everyone; empty = nobody (off).
KNOWN_ISSUES_PILOT = _list("KNOWN_ISSUES_PILOT", "")
# Who gets the "plan your task" kickoff card (server/plan.py): emails or Outlier IDs, matched to the attempt's owner
# (never an admin viewing the chat). "*" = everyone; empty = nobody (off).
PLAN_PILOT = _list("PLAN_PILOT", "")
# Who gets the /prompt fix helper (server/fix_helper.py): the fix-help offer from the 2nd /prompt FAIL on, its hidden
# rules note on their answer, and the "start your Opus run" step after a pass. Same format and matching as PLAN_PILOT.
FIX_HELPER_PILOT = _list("FIX_HELPER_PILOT", "")
# Tool-call note after /hardness (server/toolcall_note.py, 2026-11-02): emails or Outlier IDs, "*" = everyone (the
# default), empty = off.
TOOLCALL_NOTE_PILOT = _list("TOOLCALL_NOTE_PILOT", "*")
# Env data check after /hardness (server/env_check.py, 2026-11-06): same format, "*" = everyone, empty = off.
ENV_CHECK_PILOT = _list("ENV_CHECK_PILOT", "*")

DEFAULT_MODEL = _get("DEFAULT_MODEL", "claude-opus-5-5")
# Thinking effort for every run. The bundled skills pin xhigh; the portal rewrites that in its own copy.
EVAL_EFFORT = _get("EVAL_EFFORT", "high")
SMALL_MODEL = _get("SMALL_MODEL", "claude-haiku-5-5")

# Commands a user may run, in the eval's own order.
REVIEW_COMMANDS = ("injection", "prompt", "hardness", "oe", "rubric")
REPORT_FILES = {
    "injection": "0_injection.md",
    "prompt": "1_prompt.md",
    "hardness": "1a_hardness.md",
    "oe": "2_oe.md",
    "rubric": "3_rubric.md",
}
PASSING_VERDICTS = ("PASS", "NON-FAIL")


def passing(stage, verdict):
    """Whether a review's verdict counts as a pass for its stage. The oracle events need a PASS: the taxonomy's
    OE verdict has no NON-FAIL choice and its "Golden eval verdict must pass" linter wants PASS (rubric and
    hardness accept NON-FAIL), so an OE NON-FAIL counts as a fail here and gets no pass card (Evan, 2026-10-27)."""
    return verdict == "PASS" if stage == "oe" else verdict in PASSING_VERDICTS


def check():
    """Return a list of configuration problems that block startup."""
    problems = []
    for name in ("SESSION_SECRET", "PROOF_SECRET", "LITELLM_API_KEY", "REDASH_API_KEY"):
        if not globals()[name]:
            problems.append(f"{name} is not set")
    if not (EVAL_BASE_DIR / "Evals" / "WORKFLOW.md").is_file():
        problems.append(f"EVAL_BASE_DIR has no Evals/WORKFLOW.md: {EVAL_BASE_DIR}")
    if not (EVAL_BASE_DIR / "claude" / "skills").is_dir():
        problems.append(f"EVAL_BASE_DIR has no claude/skills: {EVAL_BASE_DIR}")
    for name in universe_dirs():
        if not (EVAL_BASE_DIR / name).is_dir():
            problems.append(f"missing universe folder {EVAL_BASE_DIR / name}")
    if RUNNER_MODE not in ("docker", "local"):
        problems.append("RUNNER_MODE must be docker or local")
    if REVIEW_RUNNER not in ("", "boat", "modal"):
        problems.append("REVIEW_RUNNER must be empty, boat or modal")
    if REVIEW_RUNNER in ("boat", "modal") and not PROXY_PUBLIC_URL:
        problems.append(f"REVIEW_RUNNER={REVIEW_RUNNER} needs PROXY_PUBLIC_URL")
    if REVIEW_RUNNER == "modal" and not re.fullmatch(r"[0-9a-f]{24}", MODAL_PROJECT_ID):
        problems.append("REVIEW_RUNNER=modal needs MODAL_PROJECT_ID, a real 24-hex Scale project id")
    return problems
