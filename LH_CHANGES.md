# Long Horizon Changes vs Upstream Base (`evanedreo/eval-portal` `375404a`)

Every change in `New-Eval-Portal` is recorded here by phase so future `git merge upstream/main` reviews stay clear and low-risk.

## Phase 1: Hygiene, 6-Stage Registry, Single Sonorra Universe Wiring

- `server/config.py`: Centralized `STAGES` registry (`injection`, `prompt`, `rubric`, `stumping`, `all-fail-validation`, `reference-notes`), `COMMAND_ALIASES` (`/refnotes`, `/all-fail`, `/allfail`, `/af`), `OPTIONAL_STAGES`, `FINAL_STAGE="reference-notes"`, `passing()` accepting `PASS`/`NON-FAIL` on all stages; set single `Universe_Sonorra` (`DATED_UNIVERSES=()`), `ALLOWED_PROJECT_IDS=6ae1792488162884415d5ee6`, `SERVER_BOX_ID=bx_7r4qxme3`, `BOAT_SNAPSHOT=gm-runner`, `SERVER_REVIEW_SLOTS=0`, `ASSIGNMENT_AUTOFILL=0`, and defaulted unused Atlas Redash query IDs to `0` (`COST_*`, `CB_*`, `LINTER_ALERT_*`, `SUBMIT_*`, `BUDGET_*`, `SUBMISSIONS_*`, `CLAIMS_*`).
- `server/workspace.py`: Derived `STAGE_BY_FILE` from `config.REPORT_FILES`; disabled `dated_release()` (`False`) and `UNIVERSE_DATES` (single fixed Sonorra clock).
- `server/staleness.py`: Updated `ORDER`, per-stage input dependency keys, and `LABELS` for the 6 LH stages; fixed SQL → cleared/empty reversal so clearing `injection` does not lock downstream stages; updated stale-notice copy.
- `server/progress.py`: Updated `DEFAULTS` for the 6 LH stages.
- `server/cbs.py`, `server/donedebug.py`: Updated `STEPS` / `REQUIRED` (`prompt`, `rubric`, `stumping`, `reference-notes`) and final-stage lookup (`config.FINAL_STAGE`).
- `server/assignment.py`: Mapped `UNIVERSE_BY_ID` to `long_horizon_tech_35k_scoped` (`sonorra`).
- `server/app.py`: Canonicalized `COMMAND_ALIASES` in `/api/send` before `REVIEW_COMMANDS` and `runs.command`; exposed `stages`/`aliases`/`final_stage` in `/api/me`; updated dynamic unknown-command error, waiting-review hint, funnel final-stage (`config.FINAL_STAGE`), and eval-files `Universe_Sonorra` badge.
- `server/runner.py`: Removed HarmonyGames `INJECTION_MISSING`/`INJECTION_STALE` and `oe_nonfail` report handling; updated Modal `load_universe` label to `Universe_Sonorra`.
- `web/app.js`, `web/common.js`, `web/admin.js`: Updated stage maps, dynamic gate counter, guide commands, eval-files `"Universe_"` check, admin funnel final stage (`reference-notes`), removed OE NON-FAIL override, and fixed 5 hyphen-splitting regexes so `/all-fail-validation` and `/reference-notes` (plus aliases) work across slash autocomplete and send dialogs.
- `runner/Dockerfile`: Added `python3-openpyxl` for `.xlsx` inspection.
- `deploy/*.sh`, `deploy/*.service`, `CLAUDE.md`, `.env.example`: Updated box ID (`bx_7r4qxme3`), repo (`rajukumar-scale/Geranium-Mambo-Eval`), snapshot (`gm-runner`), `Universe_Sonorra`, `LimitMEMLOCK=1G`, and 6-stage release snapshot check.
- `tests/`: Added `tests/test_lh_stages.py`; updated `test_oe_verdict.py`, `test_recover_report.py`, `test_pass_lock.py`, `test_cbs.py`, `test_submit_watch.py`, `test_submit_match.py`, `test_cost.py`, `test_linter_alerts.py` for the 6 LH stages and `0`-defaulted Redash IDs.
