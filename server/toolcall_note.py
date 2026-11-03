"""Tool-call note after /hardness (2026-11-02, Evan): no portal stage checked the tool-call minimum, so contributors found
out only at the platform baseline, after finishing every stage. The Opus run they upload for /hardness already shows
its tool-call count: after a /hardness review finishes, count it (no model involved) and, when it's below or close to
the task type's minimum, say so in the chat with a link to the Tool calls guide.

- Count: tool_use blocks in the saved run (Evals/task_inputs/opus/trajectory.json, the stream-json export /hardness
  reads). Subagents' own calls aren't in the export and aren't counted. Unknown formats post nothing.
- Minimum: Normal 50 (and 100 at most), Long-Horizon 100+, from the saved assignment's Task Type. Baseline QC averages
  the six baseline runs, so a single run near the line is a warning, not a verdict.
- Advice only: never changes a verdict or blocks anything. Off for admins' runs, load tests, archived chats, and anyone
  outside TOOLCALL_NOTE_PILOT ("*" = everyone, the default).
"""

import json
import logging
import re

from . import config, db, hub, workspace

log = logging.getLogger("portal.toolcall_note")

CLOSE = 1.2  # within 20% above the minimum: the six-run average can dip under it
TRAJECTORY = "opus/trajectory.json"
ASSIGNMENT = "00_Task_Assigned_Inputs.md"
GUIDE = "[Tool calls guide](/?guide=toolcalls)"


def count_tool_calls(text):
    """Tool calls in a run export, or None when the format isn't one we know."""
    try:
        data = json.loads(text)
        events = data if isinstance(data, list) else data.get("messages") if isinstance(data, dict) else None
    except ValueError:
        try:
            events = [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError:
            return None
    if not isinstance(events, list):
        return None
    n, seen = 0, False
    for e in events:
        if not isinstance(e, dict):
            continue
        msg = e.get("message") if e.get("type") == "assistant" else e if e.get("role") == "assistant" else None
        if not isinstance(msg, dict):
            continue
        seen = True
        n += sum(1 for b in msg.get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use")
    return n if seen else None


def task_type(text):
    t = (text or "").lower()
    m = re.search(r"task type[^a-z]*(super[- ]long[- ]horizon|long[- ]horizon|normal)", t)
    if not m:
        return None
    return "long-horizon" if "long" in m.group(1) else "normal"


def in_pilot(chat):
    pilot = {x.lower() for x in config.TOOLCALL_NOTE_PILOT}
    if not pilot or not chat:
        return False
    if "*" in pilot:
        return True
    email = (chat.get("email") or "").strip().lower()
    return bool(pilot & {x for x in (chat.get("user_id"), email, email.split("+")[0]) if x})


def note_text(n, kind):
    if kind == "long-horizon":
        low, label, rng = 100, "Long-Horizon", "100+"
    else:
        low, label, rng = 50, "Normal", "50–100"
    if n < low:
        return (f"⚠ **Tool calls: your Opus run made {n}.** A {label} task needs **{rng}** on average across the six "
                f"baseline runs, so the baseline would likely fail the tool-call check. Plan more record-level work "
                f"before /oe and /rubric: see the {GUIDE}.")
    if n < low * CLOSE:
        return (f"Heads up: **your Opus run made {n} tool calls**, close to the {label} minimum of {low}. The baseline "
                f"averages six runs and runs vary, so it could dip under. Consider adding record-level work now, before "
                f"/oe and /rubric: see the {GUIDE}.")
    if kind == "normal" and n > 100:
        return (f"Heads up: **your Opus run made {n} tool calls**, above the Normal range of 50–100 that baseline QC "
                f"checks. If the six baseline runs average over 100, the task may need trimming or a Long-Horizon "
                f"assignment: see the {GUIDE}.")
    return None


def after_review(chat, run, status):
    """Post the note after a finished /hardness review. Returns the posted message, or None. Runner._complete calls it
    inside a try: it must never break finishing a run."""
    if not run or run.get("kind") != "review" or run.get("command") != "hardness" or status != "done":
        return None
    if run.get("actor_admin") or run.get("loadtest") or not chat or chat.get("archived_from") or not in_pilot(chat):
        return None
    inputs = workspace.task_inputs(chat["attempt_id"])
    try:
        n = count_tool_calls((inputs / TRAJECTORY).read_text(encoding="utf-8", errors="replace"))
        kind = task_type((inputs / ASSIGNMENT).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if n is None or kind is None:
        return None
    text = note_text(n, kind)
    if not text:
        return None
    msg = db.add_message(chat["attempt_id"], "system", text, {"run_id": run["id"], "toolcall_note": True, "warn": True})
    hub.publish(chat["attempt_id"], "message", msg)
    log.info("tool-call note posted for %s (%s calls, %s)", chat["attempt_id"], n, kind)
    return msg
