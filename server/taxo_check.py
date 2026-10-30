"""Check the text in the contributor's taxonomy field against a pass, before they press Next.

The L-1 fingerprint linters block when the taxonomy's injection / prompt / OE / rubric isn't the version that
passed. Most injection blocks on 10-28/29 were real: same start and end, 2,000-9,000 characters different in
the middle, which a quick look calls "identical". Here the contributor (or a QM) pastes the taxonomy field and
gets the taxonomy's own fingerprint for it, and for text, where it first differs from what passed.
"""

import json

from . import fingerprint, taxo_reader

TEXT_STAGES = ("injection", "prompt", "oe")
STAGES = TEXT_STAGES + ("rubric",)


def _rubric_fp(pasted):
    """The rubric's JSON view, as a criteria list or an object holding one."""
    try:
        data = json.loads(pasted)
    except ValueError:
        raise ValueError("That isn't the rubric's JSON. Open the rubric's JSON view in the taxonomy and paste all of it.")
    if isinstance(data, dict):
        data = data.get("criteria", data.get("rubric", data))
    if not isinstance(data, list):
        raise ValueError("That JSON has no list of criteria. Paste the rubric's whole JSON view.")
    return fingerprint.rubric(data)


def compare(stage, pasted, pass_fp, saved=None):
    """pasted = the taxonomy field's text; pass_fp = the pass's stored fingerprint; saved = the portal's saved
    input for the stage if it is still the version that passed (for showing where the two differ)."""
    pasted_fp = _rubric_fp(pasted) if stage == "rubric" else fingerprint.text(stage, pasted)
    taxonomy = taxo_reader.taxonomy_line({"fingerprint": pasted_fp})
    passed = taxo_reader.taxonomy_line({"fingerprint": pass_fp})
    out = {"match": taxo_reader.matches(taxonomy, passed), "taxonomy": taxonomy, "passed": passed}
    if out["match"] or stage not in TEXT_STAGES or saved is None:
        return out
    a, b = fingerprint.normalize(pasted), fingerprint.normalize(saved)
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    out["difference"] = {"at": i + 1, "taxonomy_chars": len(a), "passed_chars": len(b),
                         "taxonomy_text": a[max(0, i - 40):i + 80], "passed_text": b[max(0, i - 40):i + 80],
                         "offset": min(40, i)}
    return out
