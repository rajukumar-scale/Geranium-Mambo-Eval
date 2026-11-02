"""Check the text in the contributor's taxonomy field against a pass, before they press Next.

The L-1 fingerprint linters block when the taxonomy's injection / prompt / OE / rubric isn't the version that
passed. Most injection blocks on 10-28/29 were real: same start and end, 2,000-9,000 characters different in
the middle, which a quick look calls "identical". Here the contributor (or a QM) pastes the taxonomy field and
gets the taxonomy's own fingerprint for it, and for text, where it first differs from what passed.
"""

import difflib
import json
import re

from . import fingerprint, taxo_reader

TEXT_STAGES = ("injection", "prompt", "oe")
STAGES = TEXT_STAGES + ("rubric",)


_OPENS = re.compile(r'^\s*(?:"[^"]*"\s*:\s*)?([\[{])\s*$')
_CLOSES = re.compile(r"^\s*[\]}]\s*,?\s*$")
_FIELD = re.compile(r'^\s*"(title|weight)"\s*:\s*(.*?)\s*,?\s*$')


def _lenient_criteria(text):
    """The taxonomy's JSON view isn't always valid JSON: it doesn't escape quotes inside titles (2026-11-01). Read
    it line by line instead: for each object directly inside a list (a criterion, not its annotations), its own
    "title": "…" line (everything between the first and last quote) and "weight" line."""
    rows, stack = [], []  # stack of [kind, fields]
    for line in text.splitlines():
        opened = _OPENS.match(line)
        if opened:
            stack.append(["list" if opened.group(1) == "[" else "obj", {}])
            continue
        if _CLOSES.match(line):
            kind, fields = stack.pop() if stack else ("obj", {})
            if kind == "obj" and stack and stack[-1][0] == "list" and "title" in fields:
                rows.append(fields)
            continue
        m = _FIELD.match(line)
        if not m or not stack or stack[-1][0] != "obj":
            continue
        key, value = m.groups()
        if key == "title":
            value = value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value
            stack[-1][1]["title"] = value.replace('\\"', '"')
        else:
            stack[-1][1]["weight"] = value.strip('"')
    return rows


def _criteria(pasted):
    """The rubric's JSON view, as a criteria list or an object holding one."""
    try:
        data = json.loads(pasted)
    except ValueError:
        data = _lenient_criteria(pasted)
        if not data:
            raise ValueError("That isn't the rubric's JSON. Open the rubric's JSON view in the taxonomy and paste all of it.")
    if isinstance(data, dict):
        data = data.get("criteria", data.get("rubric", data))
    if not isinstance(data, list):
        raise ValueError("That JSON has no list of criteria. Paste the rubric's whole JSON view.")
    return data


def _rubric_fp(pasted):
    return fingerprint.rubric(_criteria(pasted))


# The JSON view also shortens links ("https://harmonygames.slack.com/archives/…/N"), so links aren't compared row
# by row; the linter itself still checks the full text.
_URL = re.compile(r"<?(?:https?://|www\.)[^\s>]*>?")


def _rows(criteria):
    """Each row as the taxonomy's linter sees it: the title's letters and digits only (formatting, escapes, link
    targets and the builder's &lt;/&gt; don't count), links left out, and its weight (missing or 0 counts as 1)."""
    out = []
    for c in criteria:
        c = c if isinstance(c, dict) else {}
        title = fingerprint._jsonata_string(c.get("title", ""))
        bare = _URL.sub("", fingerprint._LINK_TARGET.sub("", title))
        key = fingerprint._NOT_ALNUM.sub("", fingerprint._as_builder_saves(bare))
        weight = fingerprint._jsonata_number(c.get("weight")) or 1
        out.append({"title": " ".join(title.split()), "key": key, "weight": weight})
    return out


def rubric_rows(taxonomy, passed, limit=40):
    """Which rows differ between the taxonomy's rubric and the one that passed, aligned by title (so an added or
    dropped row doesn't make every later row look different). Per-row weights are listed only when the totals
    differ: the linter compares the total, not each row."""
    a, b = _rows(taxonomy), _rows(passed)
    weights_matter = sum(r["weight"] for r in a) != sum(r["weight"] for r in b)
    out = []

    def add(kind, i=None, j=None):
        ra, rb = (a[i] if i is not None else None), (b[j] if j is not None else None)
        out.append({"kind": kind, "taxonomy_row": None if i is None else i + 1, "passed_row": None if j is None else j + 1,
                    "taxonomy_title": ra and ra["title"][:300], "passed_title": rb and rb["title"][:300],
                    "taxonomy_weight": ra and ra["weight"], "passed_weight": rb and rb["weight"]})

    matcher = difflib.SequenceMatcher(None, [r["key"] for r in a], [r["key"] for r in b], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            if weights_matter:
                for k in range(i2 - i1):
                    if a[i1 + k]["weight"] != b[j1 + k]["weight"]:
                        add("weight", i1 + k, j1 + k)
            continue
        pairs = min(i2 - i1, j2 - j1) if op == "replace" else 0
        for k in range(pairs):
            add("changed", i1 + k, j1 + k)
        for i in range(i1 + pairs, i2):
            add("extra", i, None)  # in the taxonomy, not in what passed
        for j in range(j1 + pairs, j2):
            add("missing", None, j)  # in what passed, not in the taxonomy
    return {"rows": out[:limit], "total": len(out), "taxonomy_count": len(a), "passed_count": len(b)}


def compare(stage, pasted, pass_fp, saved=None):
    """pasted = the taxonomy field's text; pass_fp = the pass's stored fingerprint; saved = the portal's saved
    input for the stage if it is still the version that passed (for showing where the two differ: the first
    differing character for text, the differing rows for a rubric)."""
    pasted_fp = _rubric_fp(pasted) if stage == "rubric" else fingerprint.text(stage, pasted)
    taxonomy = taxo_reader.taxonomy_line({"fingerprint": pasted_fp})
    passed = taxo_reader.taxonomy_line({"fingerprint": pass_fp})
    out = {"match": taxo_reader.matches(taxonomy, passed), "taxonomy": taxonomy, "passed": passed}
    if out["match"] or saved is None:
        return out
    if stage == "rubric":
        try:
            rows = rubric_rows(_criteria(pasted), _criteria(saved))
        except ValueError:
            return out
        if rows["total"] == 0 and rows["taxonomy_count"] == rows["passed_count"]:
            # Same rows, titles and weights once links are left out: the difference is the JSON view's
            # shortened links, not the rubric.
            out.update(match=True, links_ignored=True)
        else:
            out["rubric_rows"] = rows
        return out
    a = fingerprint.normalize(fingerprint.as_taxonomy_stores(stage, pasted))
    b = fingerprint.normalize(fingerprint.as_taxonomy_stores(stage, saved))
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    out["difference"] = {"at": i + 1, "taxonomy_chars": len(a), "passed_chars": len(b),
                         "taxonomy_text": a[max(0, i - 40):i + 80], "passed_text": b[max(0, i - 40):i + 80],
                         "offset": min(40, i)}
    return out
