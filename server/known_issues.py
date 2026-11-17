"""The chat page's "Known data issues" list for Sonorra v17."""

import json
from pathlib import Path

from . import broadcasts, config

PATH = Path(__file__).with_name("known_issues.json")
UNIVERSES = ("sonorra",)
_data = None


def data():
    global _data
    if _data is None:
        _data = json.loads(PATH.read_text())
    return _data


def in_pilot(session, chat):
    pilot = {x.lower() for x in config.KNOWN_ISSUES_PILOT}
    if not pilot:
        return False
    if "*" in pilot:
        return True
    email = (chat.get("email") or "").strip().lower()
    who = {session.get("user_id"), chat.get("user_id"), email, broadcasts.base_email(email), session.get("admin")}
    return bool(pilot & {str(x).lower() for x in who if x})


def view(universe=None):
    d = data()
    groups = [{
        "universe": "sonorra",
        "label": "Sonorra v17 (Aug 24, 2026 16:41 CT)",
        "date": "2026-08-24",
        "issues": [i for i in d["issues"] if i.get("universe") == "sonorra"],
    }]
    return {"enabled": True, "verified": d["verified"], "universe": "sonorra", "groups": groups}
