# Stumping Guide: Making a Task Hard for Carbon + Opus (S18)

Geranium Mambo evaluates every task against **6 base runs**: **3 `antigravity-carbon`** runs and **3 `claude-opus-5-5`** runs.

## Part 1. How Stumping (S18) Qualifies

Let:
- `carbon_score` = average of Carbon's **2 highest** runs (trimmed best-2; all 3 runs must still be graded).
- `best_opus_score` = highest score of the 3 Opus runs.
- `gap` = `best_opus_score - carbon_score`.

`/stumping` passes only when **all four gates** hold:
1. **IF failure:** At least **2 of ALL 3 Carbon runs** (including the dropped lowest run) fail at least one **weight-5** criterion because Carbon did not do what the prompt said (wrong channel or recipient, sent instead of drafting, an excluded item included, a required item left out after Carbon saw it, a broken format, order, length or count, a forbidden write). Rounding, arithmetic, a tool call Carbon got wrong while trying, tool or environment errors, crashed or empty runs, unreadable files and grader mistakes do not count.
2. **Difficulty:** the **Achieved Difficulty** (the harder of the two tiers below) is **at least your Assigned Difficulty**:
   - `gap`: `>= 15` Hard · `10–14.9` Medium · `5–9.9` Easy · `< 5` None
   - `carbon_score`: `< 75` Hard · `75–84.9` Medium · `85–89.9` Easy · `>= 90` None
3. **Carbon does not beat Opus:** `carbon_score <= best_opus_score`.
4. **No environment errors:** none of the 6 runs has a tool or platform failure: a tool that failed although the model called it correctly, a timeout, a crash, an empty or stale result caused by the platform, or a run that ended in garbage output. Re-run any such run and regrade before `/stumping`. A tool error the model caused by its own call (a missing argument, a made-up ID, too many items in one call) is fine.

**Case A / B / C:**
- **Case C (gate 1, 2 or 3 fails):** Harden the task and rerun the base runs. When only gate 4 fails, re-run the affected runs.
- **Case A (`qualifies = YES` & Opus usable):** At least 1 Opus run passes **every** weight-5 criterion and misses `<= max(3, floor(0.20 * N_w3))` weight-3 criteria (no 50% cap under QC Spec v3), and `Achieved Difficulty >= Assigned Difficulty`. Proceed to `/all-fail-validation` & `/reference-notes`.
- **Case B (`qualifies = YES` & Opus NOT usable):** Run **3 Opus-only solvability re-runs** focused on the w5/w3 criteria that failed all 3 base Opus runs. Only criteria that STILL fail all 3 solvability re-runs require justification.

## Part 2. Traps That Beat Carbon While Staying Solvable for Opus

1. **A correction somewhere else:** A Slack post, Notion page, or email announces a policy or number; a later record or different service (Linear comment, Stripe charge, QuickBooks bill) overrides or corrects it.
2. **A "done" that isn't:** A PR or ticket says complete, but the actual repo branch, Stripe payout, or file export shows a remaining gap.
3. **Cross-service reconciliation across `/mnt/files` PDF/XLSX/DOCX + APIs:** Carbon frequently skips reading attached spreadsheets/PDFs thoroughly or stops after the first matching Slack/email message.
4. **Rule-driven numeric / date filters:** Exclusions, date cutoffs before `2026-08-24T16:41:00-05:00`, timezone boundaries (`America/Chicago` CT vs UTC), or fee/tax rules that change counts and totals.
5. **Persona ACL & role realism:** Relying only on data visible to your selected persona (`Gmail`, `GCal`, `FinTrack`, `Messaging`, `Reminders` are strictly per-persona; Slack private/DMs are members-only; rest + `/mnt/files` are shared).

## 3. What Does NOT Work

- Putting instructions or hints in the prompt that point directly to the trap record.
- Unclear asks or missing defaults (those fail **S1 Action Decision Ambiguity** or create forks).
- Building on known base-data contradictions (`guidelines/known_data_issues.md`) unless explicitly scoped to a single system of record or resolved via `01_Injection.sql`.
