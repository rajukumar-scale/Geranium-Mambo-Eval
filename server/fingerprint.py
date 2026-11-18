"""Input fingerprints, the same ones the platform taxonomy shows reviewers at L10 (template v316, "added fingerprint").

Each pass card and report card carries the fingerprint of the exact input the review graded. A reviewer compares
it with the fingerprint the taxonomy computes from what was submitted: identical means the pass covers the
submitted version; different means the contributor changed it after the review (or never pasted it in).

This is a line-for-line port of the taxonomy's v316 JSONata transformers (first/last 40 characters), stored with each
pass. Since v317 the taxonomy shows first/last 4; the pages derive that exact line from the stored one (shortFp in
web/common.js, FP_SHOW = 4; checked identical to v317's JSONata on 666 cases). Transformers:
  injection  transformer-jsonata-271c4e53452d  (injection_sql)
  prompt     transformer-jsonata-84a552ee061c  (prompt.content)
  rubric     transformer-jsonata-870e0b3d907f  (rubric-criteria: [{title, weight}, ...]); titles as letters and digits
             only since 2026-10-17, because the rubric builder stores rich text (see rubric())
  oe         transformer-jsonata-b0a3d62f0196  (oracle_events)
Checked against the real JSONata engine (jsonata 2.2.2, 2026-10-17): 512 of 513 edge cases identical; the other is a
rubric weight of null, where the taxonomy's own expression errors. If the taxonomy's expressions change, change this too.
"""

import json
import math
import re

# JavaScript's \s, which the JSONata regexes use (Python's \s differs: no U+FEFF, extra \x1c-\x1f and \x85).
_JS_WS = "\t\n\v\f\r    -     　﻿"
_RUN_WS = re.compile(f"[{_JS_WS}]+")
_TRAILING = re.compile(f"[.,;:{_JS_WS}]+\\Z")
EMPTY = {
    "injection": "(empty — no injection)",
    "prompt": "(empty)",
    "oe": "(empty — no oracle events)",
    "rubric": "(empty — no rubrics)",
    "stumping": "(empty — no stumping check)",
    "all-fail-validation": "(empty — no all-fail justification)",
    "reference-notes": "(empty — no reference notes)",
}
_STAGE_SLOTS = {
    "stumping": ("stumping-check", "grading-base"),
    "all-fail-validation": ("all-fail-justify", "grading-solv"),
}


def _jsonata_trim(text):
    """JSONata's $trim: runs of space/tab/CR/LF become one space, then one leading and one trailing space go."""
    text = re.sub(r"[ \t\n\r]+", " ", text)
    if text.startswith(" "):
        text = text[1:]
    if text.endswith(" "):
        text = text[:-1]
    return text


def normalize(text):
    """$replace($replace($trim(raw), /\\s+/, " "), /[.,;:\\s]+$/, "")"""
    return _TRAILING.sub("", _RUN_WS.sub(" ", _jsonata_trim(text)))


def _head_tail(norm):
    length = len(norm)  # JSONata counts and slices by Unicode code points, like Python
    return f"{length} chars — " + (f"{norm[:40]} … {norm[-40:]}" if length > 40 else norm)


def _js_number(value):
    """JSONata's $string() of a number: 15 significant digits, integers without a decimal point."""
    value = float(f"{value:.15g}")
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def _jsonata_number(value):
    """$number(): numbers stay, numeric strings parse; anything else has no value."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            number = float(value.strip()) if value.strip() else None
        except ValueError:
            return None
        return number if number is not None and math.isfinite(number) else None
    return None


def _jsonata_string(value):
    """$string() of a title: strings as they are, anything else as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _js_number(value)
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# The taxonomy's prompt editor is markdown: text copied out of it comes with backslash escapes ("#admin_foundersonly"
# -> "#admin\_foundersonly"), but it saves the plain text, and the linter counts that (166 submitted tasks
# 2026-10-20..11-01: 0 stored escapes in prompts, underscores kept plain). A prompt copied from the taxonomy into
# the portal was one character longer per escape and the prompt fingerprint linter blocked it. So for the prompt
# the backslash of a markdown escape doesn't count. OE and injection fields store exactly what was typed.
_MD_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")


def as_taxonomy_stores(stage, raw):
    raw = raw if isinstance(raw, str) else "" if raw is None else str(raw)
    return _MD_ESCAPE.sub(r"\1", raw) if stage == "prompt" else raw


def text(stage, raw):
    """Injection / prompt / OE fingerprint of one text input."""
    norm = normalize(as_taxonomy_stores(stage, raw))
    return _head_tail(norm) if norm else EMPTY[stage]


_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]")
# The rubric builder saves a typed < or > as &lt; / &gt; ("Harmony Games <> Ludlow" -> "Harmony Games &lt;&gt; Ludlow"),
# which adds the letters "lt"/"gt" to the taxonomy's count, but keeps <https://...> autolinks as they are (40
# completed tasks, 2026-10-27: no raw < or > in any title outside an autolink; & stays raw). Write them the same way
# here, so a rubric pasted with plain <> gets the taxonomy's code. Already-encoded &lt; has no < and is left alone.
_BUILDER_ANGLE = re.compile(r"<https?://[^\s<>]*>|[<>]")


def _as_builder_saves(titles):
    return _BUILDER_ANGLE.sub(lambda m: m.group(0) if len(m.group(0)) > 1 else ("&lt;" if m.group(0) == "<" else "&gt;"), titles)


def rubric(criteria):
    """Rubric fingerprint: count, total weight, weights in order, then the titles' letters and digits only. The
    taxonomy's rubric builder stores titles as rich text (emails and sites become [x](mailto:x) links, characters
    get backslash-escaped, quotes and line breaks differ), so only letters and digits are compared, after dropping
    link targets. Same as the taxonomy's rubric transformer since 2026-10-17 ("letters")."""
    if isinstance(criteria, dict) and isinstance(criteria.get("criteria"), list):
        criteria = criteria["criteria"]  # the chat bot can save {"criteria": [...]}; the taxonomy holds just the list
    if not isinstance(criteria, list) or not criteria:
        return EMPTY["rubric"]
    weights = []
    for criterion in criteria:
        raw_w = criterion.get("weight") if isinstance(criterion, dict) else None
        if raw_w is None and isinstance(criterion, dict):
            raw_w = (criterion.get("annotations") or {}).get("weight_tier")
        number = _jsonata_number(raw_w)
        weights.append(number if number else 1)  # missing, 0 or not a number counts as 1, as in the taxonomy
    titles = " | ".join(
        _jsonata_string(c["title"] if "title" in c else c.get("criterion", ""))
        for c in criteria if isinstance(c, dict) and ("title" in c or "criterion" in c)
    )
    letters = _NOT_ALNUM.sub("", _LINK_TARGET.sub("", _as_builder_saves(titles)))
    shown = f"{letters[:40]} … {letters[-40:]}" if len(letters) > 40 else letters
    return (f"{len(criteria)} criteria, Σweight {_js_number(sum(weights))}, weights [{','.join(_js_number(w) for w in weights)}]"
            f" · Titles {len(letters)} letters — {shown}")


def for_attempt(attempt_id, stage):
    """The fingerprint of an attempt's saved input for this stage, as it is now (call it when the review ends,
    so it's the version that was graded). Returns None for stages with no matching input (proofs falls back to EVP code)."""
    if stage not in EMPTY:
        return None
    from . import workspace  # here, not at the top: workspace is a heavier module

    keys = _STAGE_SLOTS.get(stage, (stage,))
    for key in keys:
        entry = workspace.read_input(attempt_id, key, limit=200_000_000)
        files = [f for f in (entry or {}).get("files", []) if f.get("text") is not None]
        if files:
            return of_input(stage, "\n".join(f["text"] for f in files))
    return None if stage in _STAGE_SLOTS else of_input(stage, "")


def of_input(stage, content):
    """The fingerprint of a saved input file's content for a review stage, or None if there's no fingerprint."""
    if stage == "rubric":
        try:
            return rubric(json.loads(content)) if content else EMPTY["rubric"]
        except (TypeError, ValueError):
            return None  # not JSON: the taxonomy's criteria list can't be matched
    if stage in ("injection", "prompt", "oe", "stumping", "all-fail-validation", "reference-notes"):
        return text(stage, content)
    return None
