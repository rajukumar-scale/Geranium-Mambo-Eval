# Known Data Issues (Sonorra v17)

Built by deploy/build_known_issues.py from Sonorra_known_issues.md (verified 2026-10-30).
Base-data records that contradict each other. Do not grade tasks on these contradictions unless
explicitly scoped to a named system of record or settled via `01_Injection.sql`.

## Sonorra v17 (2026-08-24 16:41 CT)

### K-01 — Naomi Louisiana counsel note
- **Don't grade:** Whether Naomi holds the 2025-03-10 Louisiana counsel note
- **Why:** Naomi's mailbox holds the 2025-03-10 Louisiana counsel email + statute PDF, while S05, her Slack, MOB-104, and 2026-08-06 counsel mail say she was never on it.
- **Safer route:** Use the 2026 notes sent to Priya and Sana, or stay off whether Naomi saw the 2025-03-10 note unless settled by SQL.

### K-02 — Information-boundary leaks
- **Don't grade:** Persona ignorance claims where Slack or Gmail leaked the fact
- **Why:** Anh's two does_not_know facts appear in Slack channels he belongs to; Yusuf's Slack holds 3 private DMs he is not listed in; leaks to Naomi, Priya, Marisol, Rafa, Delia, Colin remain.
- **Safer route:** Check actual channel membership and mailbox copies directly rather than relying on universe.yaml does_not_know.

### K-03 — Records citing later events or Linear keys
- **Don't grade:** Chronology or creation-order inferences on pre-dated citations
- **Why:** 452 Linear-key citations in files and 98 in services predate the cited Linear issue; early mentions exist for the Aug 20 load test, Aug 8 incident/postmortem, ENG-588..592, ENG-611, RFC-014 r3.
- **Safer route:** Do not grade when an event was first known based on cross-references in older docs.

### K-05 — File modified times on /mnt/files
- **Don't grade:** Grading file modified timestamps against document dates
- **Why:** ~157 files in /mnt/files have filesystem modified times earlier than the dates inside their text, and 12 rows sit exactly at 2026-08-24T16:41:00-05:00.
- **Safer route:** Grade document content dates and carrying email timestamps, not filesystem mtime.

### K-07 — Bellweather settlement & $412 correction
- **Don't grade:** Exact first-report date or commission rate for Bellweather
- **Why:** $412 correction appears in Stripe in 2025/Apr 2026 before 'first reported'; July 8 paid T+2 vs T+5; 1.0% vs 1.2% commission; QBR/recon dates & edge-case counts conflict.
- **Safer route:** Scope any Bellweather ask strictly to specific named Stripe payout/charge IDs rather than cross-doc narrative timing.

### K-08 — Cactus Bloom -$4,200 make-good sign
- **Don't grade:** Whether Cactus Bloom make-good is a credit or -$4,200 payout
- **Why:** CRM, FinTrack, and QuickBooks record it as a credit, while Stripe has a -$4,200 payout and the statement nets the organizer down.
- **Safer route:** Avoid grading the sign of the Cactus Bloom make-good across systems without clarifying the exact system of record.

### K-09 — Stripe payouts vs finance statements
- **Don't grade:** Reconciling payout registers, 120 dispute notes, or 7 stale subscriptions to Stripe
- **Why:** 11 statement payout IDs are missing from Stripe; 120/122 dispute notes have no Stripe dispute; 7 active subscriptions stopped invoicing in Jan 2026; payout register has 4 rows off Stripe.
- **Safer route:** Build Stripe tasks only on verified live Stripe IDs (`stripe.json`), not unverified statement totals.

### K-10 — Spend approval & $5,000 signature line
- **Don't grade:** Who holds the $5,000 approval line or countersigning rule
- **Why:** Two conflicting answers exist on who holds the $5,000 spend approval line, and one Notion note reads as ruling out countersigning.
- **Safer route:** Settle the specific approval threshold explicitly via injection if your scenario depends on it.

### K-11 — Vendor PDFs (PagerDuty, Datadog, AWS)
- **Don't grade:** Vendor PDF seat counts, MSA numbering, or invoice dates
- **Why:** PagerDuty order form & 2025 PDFs print 4 seats/$720/Sep 30 term; Datadog quote refs are stubs; AWS IEM scope and PagerDuty invoice numbers have gaps.
- **Safer route:** Verify every vendor PDF claim word-for-word against the exact PDF in Universe_Sonorra/Users/.

### K-12 — Cost-centre workbooks vs QuickBooks
- **Don't grade:** Matching cost-centre XLSX account names or preclose Micah $4,800 row to QuickBooks
- **Why:** Cost-centre XLSX account names are not QuickBooks accounts; preclose keeps a ghost $4,800 Micah row; BILL-5208 posted on 07-26.
- **Safer route:** Do not ask the agent to treat cost-centre spreadsheet account labels as QuickBooks chart-of-accounts names.

### K-13 — Personal finance & account 9946 [watch]
- **Don't grade:** Assuming housing/insurance expenses or unpaid corporate cards in FinTrack
- **Why:** Yusuf's FinTrack shows ~96% to savings, no housing/insurance, texted spend missing from FinTrack, account 9946 frozen, corporate cards never paid.
- **Safer route:** Build FinTrack asks only on actual rows in fintrack.json or injected SQL.

### K-14 — Discovery attribution 29.4% timeline
- **Don't grade:** When discovery attribution reached 29.4%
- **Why:** Docs from Sep 2025–Jun 2026 state 'Currently 29.4%', while dated captures climb from 24.6% and reach 29.4% only in Aug 2026.
- **Safer route:** Do not grade historical attribution percentages across Sep 2025–Jun 2026 docs.

### K-16 — Branch protection & SOC 2 evidence
- **Don't grade:** Whether sonorra-core branch protection is enabled or bypassed
- **Why:** GitHub shows all 10 repos protected, while a screenshot, an email, and a CSV say sonorra-core is unprotected; Northgate registers lack a bypass column.
- **Safer route:** Cite the specific source (GitHub export vs Request 04 CSV) or avoid sonorra-core branch-protection claims.

### K-17 — GitHub histories & RFC-014 PR numbers [watch]
- **Don't grade:** Cross-repo RFC-014 revisions or mobile release numbering
- **Why:** RFC-014 revisions sit in wrong repos, two mobile release schemes coexist, and some commit histories are imported.
- **Safer route:** Verify exact repo/PR/SHAs in github_hg4.json before citing.

### K-18 — Linear duplicate keys & self-keyed titles [watch]
- **Don't grade:** Counting Linear tickets across storyline passes or empty Cycle 39
- **Why:** Storylines duplicate across passes, Cycle 39 is nearly empty, labels vs label_ids disagree in spots, 51 titles retain self-keys.
- **Safer route:** Anchor Linear asks by exact issue keys (`ENG-...`) rather than broad title/count sweeps.

### K-19 — Derived weeklies & standups vs SOT
- **Don't grade:** Treating weekly digests or old standups as current Linear/GCal state
- **Why:** 676 of 1,902 weekly entries diverge from GCal; old standups/handoffs print stale owners, titles, or deleted keys.
- **Safer route:** Grade Linear/GCal directly as system of record rather than un-regenerated weekly summaries.

### K-20 — CRM 'Logged by' & missing engagement owners [watch]
- **Don't grade:** CRM engagement ownership
- **Why:** 44 CRM bodies end with 'Logged by...' and engagements lack an owner field.
- **Safer route:** Do not require inferring CRM engagement owners from missing fields.

### K-21 — Calendar fillers & attendee-copy diffs [watch]
- **Don't grade:** Recurring calendar series or QBR/board attendees
- **Why:** No all-day/rrule events, 294 attendee-copy differences, QBR/board events lack attendees, Monday shown as office day.
- **Safer route:** Verify attendee presence on the acting persona's actual calendar events.

### K-22 — 12 unresolved Linear keys & WSDL mismatch
- **Don't grade:** Citing the 12 non-existent Linear keys or BST-341 WSDL operations
- **Why:** 12 cited Linear keys do not exist in linear.json; bypass finding has two keys; BST-341 names operations absent from WSDLs.
- **Safer route:** Confirm every cited Linear key exists in linear.json before including it in prompt/rubric.

### K-24 — universe.yaml departments & footprint counts
- **Don't grade:** Using universe.yaml department='engineering' or footprint totals
- **Why:** All 15 personas in universe.yaml list department='engineering'; 18 phone mismatches and 8 Marguerites exist in contacts; footprints are off by 1–12.
- **Safer route:** Use canonical job titles from roster.json / contacts.json and exact record counts from service exports.

### K-27 — Micah Trent engagement & BILL-9314 timing
- **Don't grade:** Micah Trent SOW dates vs BILL-9314 or June SMS
- **Why:** Measurements predate SOW; BILL-9314 precedes the engagement; SOW PDFs omit term; June SMS copy the August exchange.
- **Safer route:** Keep Micah Trent asks focused on specific invoice/code artifacts rather than engagement timeline reconciliation.

### K-29 — Packaging & current_user / leftover filenames [watch]
- **Don't grade:** Expecting labels.json or universe overview docs in service exports
- **Why:** gmail/labels.json is empty; DMs are stored as named channels; service filenames use legacy suffixes (notion_fixed, github_hg4).
- **Safer route:** Use Evals/UNIVERSE_MAP.md for canonical filenames.

### V12-C1 — Refund run 3,140 vs Stripe 62 duplicate refunds
- **Don't grade:** Expecting 3,140 refund rows in Stripe
- **Why:** Incident docs state 3,140 refunds while Stripe intentionally holds 62 duplicate refunds (cannot mint 3,140 without a second ledger).
- **Safer route:** Treat 3,140 as the documented incident report figure and 62 as the Stripe export figure.

### V12-C2 — Dispute window 160 vs Stripe 2 disputes
- **Don't grade:** Expecting 160 dispute rows in Stripe
- **Why:** Docs state 160 disputes in the window while Stripe intentionally holds 2 disputes.
- **Safer route:** Distinguish documented dispute-window counts from live Stripe dispute records.
