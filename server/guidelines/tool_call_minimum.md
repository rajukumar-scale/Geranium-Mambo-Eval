# Horizon & Constraint Count Guide

## Part 1. Constraint Count Modes (`Low` vs `High`)

In Geranium Mambo, every task sets its **Constraint Count** in **Task Setup**:
- **`Low`:** **10+ constraints** in the prompt and **10+ criteria** in `05_Rubric.json` (no upper limit).
- **`High`:** **20+ constraints** in the prompt and **20+ criteria** in `05_Rubric.json` (no upper limit).

Keep prompt constraints and rubric criteria aligned: every explicit prompt requirement must map cleanly to rubric criteria without compounding unrelated checks into one row.

## 2. Attachment Modes (`Attachment Required = YES`)

Both workspace attachment conventions are valid in Sonorra:
- **`/app/inputs`** (task attachments mounted in `/app/inputs`)
- **`/mnt/files`** (Sonorra shared/persona workspace files at `/mnt/files/Users/...`)

Verify that the prompt states filenames accurately and never sends non-Yusuf personas into `Users/yusuf/` assuming it is their personal folder.

## 3. Multi-Service Depth Without Fluff

- Anchor work across multiple Sonorra services (`Gmail`, `Slack`, `Linear`, `GitHub`, `Notion`, `CRM`, `QuickBooks`, `Stripe`, `GCal`, `Contacts`, `FinTrack`, `Messaging`, `Reminders`, `/mnt/files`).
- Check `Evals/references/tools/README.md` before assuming write capabilities:
  - **FinTrack** has 17 read tools and **0 write tools**.
  - **CRM** has **0 ticket tools** in live MCP.
  - **Filesystem** (`/mnt/files`, `/app/inputs`) uses local shell/Python tools, not MCP.
- Chronology cutoff is **`2026-08-24T16:41:00-05:00`** (`America/Chicago` / `2026-08-24 21:41 UTC`). Only scheduled items (calendar events, reminders, due dates, renewals, project targets) may sit after that clock.
