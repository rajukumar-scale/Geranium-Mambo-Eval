"""Sandbox-trouble /admin requests (Evan, 2026-11-04): about 1 in 10 chats that used /admin were a broken sandbox
("Snapshot failed (502)", a run stuck at 2/8 "Loading data", 500s from the Universe Explorer, an expired environment).
The fix is usually the same: hard-refresh the browser, then Settings → Redeploy Environment, then run the current
injection SQL again in the fresh sandbox. So the reply to such a request opens that guide right away; the admins are
still told."""

import re

# What contributors wrote in those requests (Oct 13 – Nov 4). Broad on purpose: a needless guide costs a click.
TROUBLE = re.compile(
    r"snapshot\w*\s+(?:failed|fails|error)|\b50[234]s?\b|bad gateway|"
    r"stuck (?:at|on) (?:step )?\d\s*/\s*8|loading data|first steps|"
    r"can'?t reach|cannot reach|unable to reach|"
    r"(?:re)?deploy\w*\s+(?:\w+\s+){0,2}(?:fail\w*|stuck|error|not working|doesn'?t work)|"
    r"(?:environment|sandbox|universe|explorer|agent runner)\s+(?:\w+\s+){0,6}"
    r"(?:expired|not loading|not running|won'?t load|isn'?t loading|not working|broken|down|error|crash\w*|stuck)|"
    r"reset (?:my |the )?(?:task )?(?:environment|sandbox)|no artifact recorded|"
    r"artifact\s+\S+\s+(?:version\s*=\s*\d+\s+)?not found|"
    r"fail\w*\s+to\s+(?:re)?deploy|"  # redeployed from a deleted snapshot (2026-11-06)
    r"(?:agents?|opus|haiku)\s+(?:runs?\s+|trajector\w*\s+)?(?:\w+\s+){0,2}(?:fail\w*|stuck|crash\w*|erroring|errors?\b)|"
    # their injection isn't in the runs: the same fix (a fresh sandbox, then the current SQL once)
    r"(?:runs?|opus|agent|verifier|rollouts?|environment|sandbox|runner)\b[^.\n]{0,60}"
    r"(?:not|n't|ignor\w*|skip\w*|missing|without|old(?:er)?)\b[^.\n]{0,40}(?:inject\w*|sql|seed\w*|overlay)|"
    r"inject\w*[^.\n]{0,40}(?:not (?:loading|applied|showing|seeded)|isn'?t (?:loading|being applied|showing)|fail\w* to load|keeps failing)",
    re.I)
# Our own portal's errors (the review service, not their sandbox): the sandbox guide doesn't help there.
PORTAL = re.compile(r"no space left|errno|run didn'?t finish|eval portal|/(?:prompt|rubric|stumping|all-fail-validation|all-fail|af|reference-notes|refnotes|oe|hardness|injection)\b[^.\n]{0,40}(?:fail|error)", re.I)


def is_sandbox_trouble(text):
    text = text or ""
    return bool(TROUBLE.search(text)) and not PORTAL.search(text)


REPLY_NOTE = ("\n\n🛠️ **While you wait:** most sandbox problems (Snapshot failed 502, a run stuck at Loading data, "
              "500 errors, an expired environment, runs without your injection) go away with a hard refresh, then "
              "**Settings → Redeploy Environment** from the base universe (never a snapshot), then your current injection SQL once. The steps just opened; you can open them again with the "
              "button below.")
