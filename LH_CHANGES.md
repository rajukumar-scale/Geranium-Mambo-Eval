# Long Horizon Changes vs Upstream Base (`evanedreo/eval-portal` `375404a`)

Every change in `New-Eval-Portal` is recorded here by phase so future `git merge upstream/main` reviews stay clear and low-risk.

## Phase 1: Hygiene, 6-Stage Registry, Single Sonorra Universe Wiring

- `server/config.py`: Centralized `STAGES` registry (`injection`, `prompt`, `rubric`, `stumping`, `all-fail-validation`, `reference-notes`), `COMMAND_ALIASES` (`/refnotes`, `/all-fail`, `/allfail`, `/af`), `OPTIONAL_STAGES`, `FINAL_STAGE="reference-notes"`, `passing()` accepting `PASS`/`NON-FAIL` on all stages; set single `Universe_Sonorra` (`DATED_UNIVERSES=()`), `ALLOWED_PROJECT_IDS=6ae1792488162884415d5ee6`, `SERVER_BOX_ID=bx_7r4qxme3`, `BOAT_SNAPSHOT=gm-runner`, `SERVER_REVIEW_SLOTS=0`, `ASSIGNMENT_AUTOFILL=0`.
- `server/workspace.py`: Derived `STAGE_BY_FILE` from `config.REPORT_FILES`; disabled `dated_release()` (`False`) and `UNIVERSE_DATES` (single fixed Sonorra clock).
- `server/staleness.py`: Updated `ORDER`, per-stage input dependency keys, and `LABELS` for the 6 LH stages; fixed SQL → cleared/empty reversal (`_NO_INJECTION_DIGESTS`) so clearing `injection` does not lock downstream stages; updated stale-notice copy.
- `server/progress.py`: Updated `DEFAULTS` for the 6 LH stages.
- `server/cbs.py`, `server/donedebug.py`: Updated `STEPS` / `REQUIRED` (`prompt`, `rubric`, `stumping`, `reference-notes`) and final-stage lookup (`config.FINAL_STAGE`).
- `server/assignment.py`: Mapped `UNIVERSE_BY_ID` to `long_horizon_tech_35k_scoped` (`sonorra`).
- `server/app.py`: Canonicalized `COMMAND_ALIASES` in `/api/send` before `REVIEW_COMMANDS` and `runs.command`; exposed `stages`/`aliases`/`final_stage` in `/api/me`; updated dynamic unknown-command error, waiting-review hint, funnel final-stage (`config.FINAL_STAGE`), and eval-files `Universe_Sonorra` badge.
- `server/runner.py`: Removed HarmonyGames `INJECTION_MISSING`/`INJECTION_STALE` and `oe_nonfail` report handling; updated Modal `load_universe` label to `Universe_Sonorra`.
- `web/app.js`, `web/common.js`, `web/admin.js`: Updated stage maps, dynamic gate counter, guide commands, eval-files `"Universe_"` check, admin funnel final stage (`reference-notes`), removed OE NON-FAIL override, and fixed 5 hyphen-splitting regexes so `/all-fail-validation` and `/reference-notes` (plus aliases) work across slash autocomplete and send dialogs.
- `runner/Dockerfile`: Added `python3-openpyxl` for `.xlsx` inspection.
- `deploy/*.sh`, `deploy/*.service`, `CLAUDE.md`, `.env.example`: Updated box ID (`bx_7r4qxme3`), repo (`rajukumar-scale/Geranium-Mambo-Eval`), snapshot (`gm-runner`), `Universe_Sonorra`, `LimitMEMLOCK=1G`, and 6-stage release snapshot check.
- `tests/`: Added `tests/test_lh_stages.py`; updated `test_oe_verdict.py`, `test_recover_report.py`, `test_pass_lock.py`, `test_cbs.py`, `test_submit_watch.py`, `test_submit_match.py`, `test_cost.py`, `test_linter_alerts.py`.

## Phase 3: Task Setup (`Your Assignment`), `GUARD`, Guides & Sonorra Known Issues

- `server/workspace.py`, `server/app.py`, `web/index.html`, `web/app.js`, `web/style.css`: Replaced `#chip-date`/`pickDate` with Geranium Mambo Task Setup (`POST /api/task-setup`, `00_Task_Assigned_Inputs.md`: 15 Sonorra personas, all 7 Task Families, `Constraint Count` `Low` 10+ / `High` 20+, plus optional `Assigned Difficulty` `Easy`/`Medium`/`Hard` and `Attachment Required` `NO`/`YES`).
- `server/runner.py`, `server/app.py`, `server/env_help.py`, `server/status.py`, `server/plan.py`: Rewrote `GUARD`, `HELP`, and status/help strings for Geranium Mambo / Sonorra v17 (`2026-08-24T16:41:00-05:00` CT) and `QC Spec doc final v3.csv`.
- `server/prompts/*.md`, `server/guidelines/*.md`: Rewrote `early_guard.md`, `guide_intro.md`, `plan_kickoff_card.md`, `fix_helper_rules.md`, `hardness_v2.md`, `tool_call_minimum.md`, `distinct_failure_patterns.md`, and `known_data_issues.md`.
- `deploy/build_known_issues.py`, `server/known_issues.py`, `server/known_issues.json`: Built 24 Sonorra base issues (`K-01..K-29` + `V12-C1..C2`).
- `web/{admin,cbs,cost,index,status,verify}.html`: Updated titles and branding to `Geranium Mambo Eval Portal`.
- `tests/`: Added `tests/test_task_setup.py`; updated `test_guide_intro.py`, `test_guides.py`, `test_inject_reminder.py`, `test_known_issues.py`.

## Phase 5: Pass-Card Fingerprints, Proofs, Taxonomy Reader & Proxy Hardening

- `server/fingerprint.py`, `server/proofs.py`: Added deterministic fingerprints for `stumping` (`07_Stumping_Check.md`), `all-fail-validation` (`11_All-Fail-Rubrics-Justification.md`), and `reference-notes` (`12_Reference_Notes.md`), and bound `prompt_fingerprint` across all downstream stages.
- `server/taxo_check.py`, `server/taxo_reader.py`, `server/taxo_reader_prompts.json`: Added all 6 LH stage reader prompts (synced word-for-word with `Taxonomy/Taxonomy.json`, including `/rubric` `Titles N letters` fix) and added `x-litellm-tags` header (`projectId:6ae1792488162884415d5ee6,useCase:eval-portal`).
- `server/proxy.py`: Added `ALLOWED_PROJECT_IDS[0]` fallback tag and `sk-...` secret redaction on upstream 4xx/5xx errors.
- `tests/test_lh_phase5.py`: Added Phase 5 unit tests.

## Phase 6 & 7 Preflight: LH Redash Queries & CI Portability

- `server/config.py`, `server/submit_match.py`, `deploy/similarity_refresh.sh`, `.env.example`: Wired verified LH Redash query IDs (`387117` login, `397698` budget, `397699` daily budget, `397700` portal SMIG cost, `397701` LH cost facts, `388752` submissions, `388856` claims, `404917` similarity refresh, `404918` submit-match with `REFNOTES_B64`).
- `tests/test_lh_phase6.py`: Added Redash & submit-match unit tests; ensured all tests run portably on clean Ubuntu CI without external local paths.
