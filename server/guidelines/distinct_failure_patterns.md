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

## 3. All-Fail Validation (S22) & Case B Solvability

- Every criterion (any weight `1`, `3`, or `5`) that **fails all completed non-errored runs** (all 6 base runs + any Case B solvability re-runs) must be checked against the prompt and `Universe_Sonorra` (+ optional `01_Injection.sql`) and have an accurate justification in `11_All-Fail-Rubrics-Justification.md` (or `12_Reference_Notes.md` in Case A when Step 61 is hidden).
- In **Case B**, run **3 Opus-only solvability re-runs** on the w5/w3 criteria that failed all 3 base Opus runs; only criteria that still fail all 3 re-runs require justification.
