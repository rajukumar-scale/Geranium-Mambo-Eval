# Contributor guidelines for the chat assistant

The assistant reads these when a contributor asks how to make a task harder, gets a too-easy result, or asks about
tool-call counts. They live in server/guidelines (deploy copies server/). Every review workspace gets this folder at `guidelines/` (next to `Evals/`): boat runs pack it into the
bundle (server/boatrun.py), docker runs mount it read-only (server/workspace.py). It is portal-owned, so changing a
guideline is a normal commit; no eval release is needed. The eval's own files and pins are not touched.

- hardness_v2.md: CB Guideline: Making a Task Hard for Opus 5.5 (Hardness v2), 2026-10-31. Built from the 46
  completed/L12 Atlas Commercial tasks where Opus 5.5 scored under 75% weighted.
- tool_call_minimum.md: CB Guideline: Reaching the Tool-Call Minimum (50 or 100), 2026-10-30. Built from 10 tasks and
  their Opus 5.5 baseline runs.
