"""Check the text in the contributor's taxonomy field against a pass, before they press Next.

The L-1 fingerprint linters block when the taxonomy's injection / prompt / OE / rubric isn't the version that
passed. Most injection blocks on 10-28/29 were real: same start and end, 2,000-9,000 characters different in
the middle, which a quick look calls "identical". Here the contributor (or a QM) pastes the taxonomy field and
gets the taxonomy's own fingerprint for it, and for text, where it first differs from what passed.
"""

import difflib
import json

from . import fingerprint, taxo_reader

TEXT_STAGES = ("injection", "prompt", "oe")
STAGES = TEXT_STAGES + ("rubric",)


def _criteria(pasted):
    """The rubric's JSON view, as a criteria list or an object holding one."""
    try:
        data = json.loads(pasted)
    except ValueError:
        raise ValueError("That isn't the rubric's JSON. Open the rubric's JSON view in the taxonomy and paste all of it.")
    if isinstance(data, dict):
        data = data.get("criteria", data.get("rubric", data))
    if not isinstance(data, list):
        raise ValueError("That JSON has no list of criteria. Paste the rubric's whole JSON view.")
    return data


def _rubric_fp(pasted):
    return fingerprint.rubric(_criteria(pasted))


def _rows(criteria):
    """Each row as the taxonomy's linter sees it: the title's letters and digits only (formatting, escapes, link
    targets and the builder's &lt;/&gt; don't count) and its weight (missing or 0 counts as 1)."""
    out = []
    for c in criteria:
        c = c if isinstance(c, dict) else {}
        title = fingerprint._jsonata_string(c.get("title", ""))
        key = fingerprint._NOT_ALNUM.sub("", fingerprint._LINK_TARGET.sub("", fingerprint._as_builder_saves(title)))
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
            out["rubric_rows"] = rubric_rows(_criteria(pasted), _criteria(saved))
        except ValueError:
            pass
        return out
    a, b = fingerprint.normalize(pasted), fingerprint.normalize(saved)
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    out["difference"] = {"at": i + 1, "taxonomy_chars": len(a), "passed_chars": len(b),
                         "taxonomy_text": a[max(0, i - 40):i + 80], "passed_text": b[max(0, i - 40):i + 80],
                         "offset": min(40, i)}
    return out
