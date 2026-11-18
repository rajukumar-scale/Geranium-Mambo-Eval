# Rubric & All-Fail Validation Guide

## 1. Rubric Format (`05_Rubric.json`)

Geranium Mambo rubrics contain **only two fields per criterion**:
- `title`: crisp, self-contained, verifiable requirement
- `weight`: integer `1`, `3`, or `5`

Do not add extra keys (`category`, `justification`, `evidence` belong in Notes/Justifications, not `05_Rubric.json`).

## 2. Rubric Quality Checks (S17 & S21)

- **S17 Prompt–Rubric Alignment:** Every explicit constraint in `03_Prompt.md` is covered by the rubric, and every rubric criterion traces back to the prompt and valid universe/SQL ground truth (no hidden or invented requirements).
- **S21 Non-Compounded & Verifiable Criteria:**
  - `0` issues → `PASS (5)`
  - `1+` minor/localized issue within percentage cutoffs → `NON-FAIL (3)`
  - Exceeding cutoffs or major structural failure → `FAIL (1–2)`
- Split distinct findings into atomic criteria so a run that gets part A right and misses part B is graded accurately.
- Never weight a formatting nit as `5`, and never hide a core decision/deliverable at `1`.

## 3. All-Fail Validation (S22) & Case B Solvability (QC Spec v3)

- In **Case B**, run **3 Opus-only solvability re-runs** on the w5/w3 criteria that failed all 3 base Opus runs; targets passed in `>= 1` re-run need NO justification.
- Targets that still fail every re-run are rated `Passable - yes` or `Passable - no` with a factual reason (`Passable - no` fails S18; S22 rates `Passable - yes` targets and reasons).
- If `Justify Solvability Targets` does not appear (Case A, Case C, or Case B with all targets passing in a re-run), S22 is automatically `5`.
