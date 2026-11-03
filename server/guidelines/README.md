# Contributor guidelines for the chat assistant

The assistant reads these when a contributor asks how to make a task harder, gets a too-easy result, or asks about
tool-call counts. They live in server/guidelines (deploy copies server/). Every review workspace gets this folder at `guidelines/` (next to `Evals/`): boat runs pack it into the
bundle (server/boatrun.py), docker runs mount it read-only (server/workspace.py). It is portal-owned, so changing a
guideline is a normal commit; no eval release is needed. The eval's own files and pins are not touched.

- hardness_v2.md: CB Guideline: Making a Task Hard for Opus 5.5 (Hardness v2), 2026-10-31. Built from the 46
  completed/L12 Atlas Commercial tasks where Opus 5.5 scored under 75% weighted.
- tool_call_minimum.md: CB Guideline: Reaching the Tool-Call Minimum (50 or 100), 2026-10-30. Built from 10 tasks and
  their Opus 5.5 baseline runs.
- distinct_failure_patterns.md: CB Guideline: Distinct Failure Patterns (Spec V2), 2026-10-08. How the six baseline
  rollouts' failure patterns are counted and how to build traps that split them. Converted from Evan's PDF on 2026-11-02.
- baseline_check.py: counts a pasted platform baseline the way baseline QC does (hardness, pass@1, tool calls, failure
  patterns); the assistant runs it when a contributor brings their baseline results (GUARD). Not a guide tab. Packed
  with the .md files (boatrun, workspace).
- known_data_issues.md: the "Known data issues" list (base-data conflicts, same ids as the chat sidebar), grouped by
  task date. Generated with server/known_issues.json by deploy/build_known_issues.py; edit the script, not the file.

Contributors can read the same four files in the chat page's Guide panel (tabs Hardness, Tool calls, Failure
patterns, Known data issues), served by GET /api/guides/<name> (app.py GUIDES allowlist). Links to /?guide=<tab>#<heading-slug> open a tab at a heading.
