"""Set the thinking effort of the bundled skills in a portal-owned copy of claude/skills.

The skills pin `effort: xhigh` in their frontmatter; the portal runs them at EVAL_EFFORT
(default high). Only the frontmatter `effort:` line is changed, never the skill body,
and never the original New_Evals bundle.

The eval pins the SHA-256 of its own files: Evals/references/prompt_rubric_release.json lists
the prompt/oe/rubric SKILL.md hashes, and package_validation.json pins those and the release
manifest itself. A stale pin makes /prompt, /oe and /rubric refuse to score ("Eval package
changed"), so every pin of a changed file is rewritten too, following the chain.

    python3 -m server.skill_effort <skills_dir> <effort>
"""

import hashlib
import re
import sys
from pathlib import Path

LEVELS = ("low", "medium", "high", "xhigh", "max")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(skills_dir, effort):
    if effort not in LEVELS:
        raise ValueError(f"effort must be one of {', '.join(LEVELS)}")
    skills_dir = Path(skills_dir)
    changed, renamed = [], {}  # renamed: old sha256 -> new sha256
    for skill in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill.read_text()
        match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not match:
            continue
        front = match.group(1)
        new_front = re.sub(r"(?m)^effort:\s*\S+\s*$", f"effort: {effort}", front)
        if new_front != front:
            old = _sha(skill)
            skill.write_text(text.replace(front, new_front, 1))
            renamed[old] = _sha(skill)
            changed.append(skill.parent.name)
    refresh_pins(skills_dir.parent.parent / "Evals", renamed)
    return changed


def refresh_pins(evals_dir, renamed):
    """Replace every pinned hash of a changed file in the eval's JSON manifests. A manifest that
    changes gets a new hash itself, so repeat until nothing else changes."""
    manifests = [p for p in Path(evals_dir).rglob("*.json")
                 if not {"task_inputs", ".cache", "__pycache__"} & set(p.parts)]
    for _ in range(10):
        if not renamed:
            return
        following = {}
        for path in manifests:
            text = path.read_text()
            new = text
            for old, now in renamed.items():
                new = new.replace(old, now)
            if new != text:
                before = _sha(path)
                path.write_text(new)
                following[before] = _sha(path)
        renamed = following
    raise RuntimeError("hash pins did not settle")


if __name__ == "__main__":
    print(apply(sys.argv[1], sys.argv[2]))
