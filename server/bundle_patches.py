"""Portal-owned fixes to tools in an installed eval release, applied after each eval-files update
(like skill_effort). The original New_Evals bundle is never changed.

Each fix applies only to the exact file it was written for (by SHA-256), so a release that already
has the fix, or a newer version of the tool, is left alone. The eval pins the hashes of its own
files, so every pin of a patched file is refreshed too (skill_effort.refresh_pins).

    python3 -m server.bundle_patches <release dir>
"""

import hashlib
import sys
from pathlib import Path

from .skill_effort import refresh_pins

# hg_records.py: after the scan, writing the per-check caches rebuilt the set of a check's source files
# once per scanned file (the set sat inside the list comprehension's condition). A messages/*.json check
# over Slack (5,353 files) made that ~29 million path operations, about 10 minutes of CPU per check, so
# /injection sat on "Retry record checks once" for 20+ minutes and contributors cancelled (2026-10-18).
# Same result; the set is built once per check.
HG_RECORDS_OLD = """\
            one = {**result, 'checks': {'check': {**results[check['id']], 'scope': canonical}},
                   'scan_complete': results[check['id']]['scan_complete'],
                   'sources': [row for row in receipts if row['file'] in
                               {(all_jobs[key]['folder'] / all_jobs[key]['info']['path']).relative_to(h.ROOT).as_posix()
                                for key in keys}]}
"""
HG_RECORDS_NEW = """\
            wanted = {(all_jobs[key]['folder'] / all_jobs[key]['info']['path']).relative_to(h.ROOT).as_posix()
                      for key in keys}
            one = {**result, 'checks': {'check': {**results[check['id']], 'scope': canonical}},
                   'scan_complete': results[check['id']]['scan_complete'],
                   'sources': [row for row in receipts if row['file'] in wanted]}
"""

PATCHES = [  # (file in the release, SHA-256 of the version the fix is for, old text, new text)
    ("Evals/tools/hg_records.py", "86b931508e9c906a051ef904fcb8d1b7739e3593354a01118265e60474c3139d",
     HG_RECORDS_OLD, HG_RECORDS_NEW),
]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def apply(release):
    """Patch the release in place; returns the files changed."""
    release = Path(release)
    changed, renamed = [], {}
    for rel, expected, old, new in PATCHES:
        path = release / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if _sha(data) != expected:
            continue
        text = data.decode()
        if text.count(old) != 1:
            raise RuntimeError(f"{rel}: the text to fix is not where expected")
        path.write_text(text.replace(old, new, 1))
        renamed[expected] = _sha(path.read_bytes())
        changed.append(rel)
    refresh_pins(release / "Evals", renamed)
    return changed


if __name__ == "__main__":
    print(apply(sys.argv[1]))
