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
# Folder holding Evals/, claude/, .claude/ (from install.py) and Universe_Sonorra/.
# Not resolved on purpose: on the VM this is a symlink to the current bundle version,
# so updating the eval swaps it for new runs without touching runs in progress.
EVAL_BASE_DIR = Path(os.path.abspath(_get("EVAL_BASE_DIR", ROOT.parent / "New-Evals-Bundle")))
# Contributor guidelines the chat assistant reads. Portal-owned, not part of the eval
# release: every review workspace gets them at work/guidelines (boat bundle, docker mount, local workspace).
GUIDELINES_DIR = ROOT / "server" / "guidelines"
# Portal-owned text for the "plan your task" kickoff (server/plan.py): the card, the hidden note behind its button and
# the early guard appended to the assistant's rules. Read by the portal itself; not copied into review workspaces.
PROMPTS_DIR = ROOT / "server" / "prompts"
UNIVERSE_DIRS = _list("UNIVERSE_DIRS", "Universe_Sonorra")
DATED_UNIVERSES = ()


def universe_dirs(base=None):
    """The universe folders of the installed release (mounted at /work/<name>)."""
    return UNIVERSE_DIRS

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
# Platform assignment autofill is off for Geranium Mambo (contributors pick via Welcome 3-dropdown Task Setup).
REDASH_TASK_QUERY_ID = _int("REDASH_TASK_QUERY_ID", 0)
ASSIGNMENT_AUTOFILL = _int("ASSIGNMENT_AUTOFILL", 0)
BUDGET_QUERY_ID = _int("BUDGET_QUERY_ID", 397698)
BUDGET_DAILY_QUERY_ID = _int("BUDGET_DAILY_QUERY_ID", 397699)
BACKUP_BUDGET_QUERY_ID = _int("BACKUP_BUDGET_QUERY_ID", 0)
COST_FACTS_QUERY_ID = _int("COST_FACTS_QUERY_ID", 397701)
COST_PORTAL_SMIG_QUERY_ID = _int("COST_PORTAL_SMIG_QUERY_ID", 397700)
COST_TASK_STATES_QUERY_ID = _int("COST_TASK_STATES_QUERY_ID", 405007)
CB_FACTS_QUERY_ID = _int("CB_FACTS_QUERY_ID", 405006)
BUDGET_KEY_ALIAS = _get("BUDGET_KEY_ALIAS", "pub-agentic-if-task")
SUBMISSIONS_QUERY_ID = _int("SUBMISSIONS_QUERY_ID", 388752)
CLAIMS_QUERY_ID = _int("CLAIMS_QUERY_ID", 388856)
# Empty list = any project.
ALLOWED_PROJECT_IDS = _list("ALLOWED_PROJECT_IDS", "6ae1792488162884415d5ee6")

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
LINTER_ALERT_QUERY_ID = _int("LINTER_ALERT_QUERY_ID", 0)  # 0 = off
LINTER_ALERT_SLACK_USER = _get("LINTER_ALERT_SLACK_USER", "")
LINTER_ALERT_EVERY_MIN = _int("LINTER_ALERT_EVERY_MIN", 15)
LINTER_ALERT_REPEAT_HOURS = _int("LINTER_ALERT_REPEAT_HOURS", 3)  # same CB/task/stage again only after this
SUBMIT_WATCH = _int("SUBMIT_WATCH", 0)
SUBMIT_MATCH_QUERY_ID = _int("SUBMIT_MATCH_QUERY_ID", 404918)
LINTER_ALERT_DASHBOARD_URL = _get("LINTER_ALERT_DASHBOARD_URL", "")
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
SERVER_BOX_ID = _get("SERVER_BOX_ID", "") or os.environ.get("BOAT_ID", "") or "bx_7r4qxme3"
BOAT_SNAPSHOT = _get("BOAT_SNAPSHOT", "gm-runner")
BOAT_BOX_TYPE = _get("BOAT_BOX_TYPE", "default")
MAX_BOAT_RUNS = _int("MAX_BOAT_RUNS", 20)
# 0 slots = all reviews run on boat boxes (safe on an 8 GB server box).
SERVER_REVIEW_SLOTS = _int("SERVER_REVIEW_SLOTS", 0)
SERVER_REVIEW_COMMANDS = _list("SERVER_REVIEW_COMMANDS", "injection")
SERVER_REVIEW_MIN_FREE_GB = float(_get("SERVER_REVIEW_MIN_FREE_GB", 5))
# Public URL of the metering proxy, as reached from review boxes (e.g. https://<id>-8001.on.boat.dev).
PROXY_PUBLIC_URL = _get("PROXY_PUBLIC_URL", "").rstrip("/")
BOAT_RESTRICT_EGRESS = _get("BOAT_RESTRICT_EGRESS", "1") == "1"
MODAL_PROXY_URL = _get("MODAL_PROXY_URL", "https://modal-proxy.internal.scale.com").rstrip("/")
MODAL_AWS_PROFILE = _get("MODAL_AWS_PROFILE", "")
MODAL_BASE_IMAGE = _get("MODAL_BASE_IMAGE", "ubuntu:24.04")
MODAL_TEMPLATE_IMAGE = _get("MODAL_TEMPLATE_IMAGE", "")
MODAL_PROJECT_ID = _get("MODAL_PROJECT_ID", "6ae1792488162884415d5ee6")
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
CB_SPEND_LIMIT_USD = float(_get("CB_SPEND_LIMIT_USD", 200))  # per attempt; past it, runs need an admin's approval (2026-11-03; $200 since 2026-11-23)
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
EVAL_ZIP_URL = _get("EVAL_ZIP_URL", "https://static.remotasks.com/uploads/6ae1792488162884415d5ee6").rstrip("/")
EVAL_FILES = ("Evals", "claude", "Universe_Sonorra")
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
# Tool-call note & env data check (/hardness only; inactive on Long Horizon commands).
TOOLCALL_NOTE_PILOT = _list("TOOLCALL_NOTE_PILOT", "*")
ENV_CHECK_PILOT = _list("ENV_CHECK_PILOT", "*")

DEFAULT_MODEL = _get("DEFAULT_MODEL", "claude-opus-5-5")
# Thinking effort for every run. The bundled skills pin effort; the portal rewrites that in its own copy.
EVAL_EFFORT = _get("EVAL_EFFORT", "high")
SMALL_MODEL = _get("SMALL_MODEL", "claude-haiku-5-5")

# Central stage registry: (id, report_file, label, timeout_min, optional).
STAGES = (
    ("injection", "0_injection.md", "Injection", 20, True),
    ("prompt", "1_prompt.md", "Prompt", 150, False),
    ("rubric", "2_rubric.md", "Rubric", 150, False),
    ("stumping", "3_stumping.md", "Stumping", 180, False),
    ("all-fail-validation", "4_all_fail.md", "All-Fail Validation", 120, True),
    ("reference-notes", "5_refnotes.md", "Reference Notes", 120, False),
)
REVIEW_COMMANDS = tuple(s[0] for s in STAGES)
REPORT_FILES = {s[0]: s[1] for s in STAGES}
STAGE_LABELS = {s[0]: s[2] for s in STAGES}
STAGE_TIMEOUT_MIN = {s[0]: s[3] for s in STAGES}
OPTIONAL_STAGES = frozenset(s[0] for s in STAGES if s[4])
FINAL_STAGE = "reference-notes"
COMMAND_ALIASES = {
    "refnotes": "reference-notes",
    "all-fail": "all-fail-validation",
    "allfail": "all-fail-validation",
    "af": "all-fail-validation",
}
PASSING_VERDICTS = ("PASS", "NON-FAIL")


def canonical_command(cmd):
    return COMMAND_ALIASES.get(cmd, cmd)


def passing(stage, verdict):
    """Whether a review's verdict counts as a pass for its stage (PASS or NON-FAIL)."""
    return verdict in PASSING_VERDICTS


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
