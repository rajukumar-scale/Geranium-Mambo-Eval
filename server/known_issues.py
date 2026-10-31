"""The chat page's "Known data issues" list: base-data records that contradict each other, so a task graded on them
usually fails Cross-Service Coherence. A pilot for now (KNOWN_ISSUES_PILOT).

known_issues.json is built by deploy/build_known_issues.py from the base-data review (2026-10-30: 6,396 portal reports,
both sides of every issue checked in the universe exports). Edit the script, not the JSON."""

import json
from pathlib import Path

from . import broadcasts, config, workspace

PATH = Path(__file__).with_name("known_issues.json")
UNIVERSES = ("synthcut", "normal")  # the keys of workspace.UNIVERSE_DATES, in the order shown when no date is picked
_data = None


def data():
    global _data
    if _data is None:
        _data = json.loads(PATH.read_text())
    return _data


def in_pilot(session, chat):
    """KNOWN_ISSUES_PILOT lists emails or Outlier IDs ("*" = everyone, empty = off). Matched against the signed-in
    person: their Outlier ID, the attempt's email (with or without the +outlier suffix), or the admin login."""
    pilot = {x.lower() for x in config.KNOWN_ISSUES_PILOT}
    if not pilot:
        return False
    if "*" in pilot:
        return True
    email = (chat.get("email") or "").strip().lower()
    who = {session.get("user_id"), chat.get("user_id"), email, broadcasts.base_email(email), session.get("admin")}
    return bool(pilot & {str(x).lower() for x in who if x})


def view(universe):
    """The list for one universe ("synthcut" / "normal"), or both, grouped, when the task date isn't picked yet."""
    d = data()
    picked = [universe] if universe in UNIVERSES else list(UNIVERSES)
    groups = [{"universe": u, "label": workspace.UNIVERSE_DATES[u][1], "date": workspace.UNIVERSE_DATES[u][0],
               "issues": [i for i in d["issues"] if i["universe"] == u]} for u in picked]
    return {"enabled": True, "verified": d["verified"], "universe": universe if universe in UNIVERSES else None,
            "groups": groups}
