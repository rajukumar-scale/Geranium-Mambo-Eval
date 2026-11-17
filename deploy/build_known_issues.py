#!/usr/bin/env python3
"""Build server/known_issues.json and server/guidelines/known_data_issues.md from Sonorra_known_issues.md."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "server" / "known_issues.json"
MD_OUT = ROOT / "server" / "guidelines" / "known_data_issues.md"
VERIFIED = "2026-10-30"

# Curated Sonorra v17 base-data issues (K-01..K-29 + V12-C1..C2) from Base Universe/Sonorra_known_issues.md.
SONORRA_ISSUES = [
    ("K-01", "Naomi Louisiana counsel note", "avoid", 1,
     "Whether Naomi holds the 2025-03-10 Louisiana counsel note",
     "Naomi's mailbox holds the 2025-03-10 Louisiana counsel email + statute PDF, while S05, her Slack, MOB-104, and 2026-08-06 counsel mail say she was never on it.",
     "Use the 2026 notes sent to Priya and Sana, or stay off whether Naomi saw the 2025-03-10 note unless settled by SQL."),
    ("K-02", "Information-boundary leaks", "avoid", 1,
     "Persona ignorance claims where Slack or Gmail leaked the fact",
     "Anh's two does_not_know facts appear in Slack channels he belongs to; Yusuf's Slack holds 3 private DMs he is not listed in; leaks to Naomi, Priya, Marisol, Rafa, Delia, Colin remain.",
     "Check actual channel membership and mailbox copies directly rather than relying on universe.yaml does_not_know."),
    ("K-03", "Records citing later events or Linear keys", "avoid", 1,
     "Chronology or creation-order inferences on pre-dated citations",
     "452 Linear-key citations in files and 98 in services predate the cited Linear issue; early mentions exist for the Aug 20 load test, Aug 8 incident/postmortem, ENG-588..592, ENG-611, RFC-014 r3.",
     "Do not grade when an event was first known based on cross-references in older docs."),
    ("K-05", "File modified times on /mnt/files", "avoid", 1,
     "Grading file modified timestamps against document dates",
     "~157 files in /mnt/files have filesystem modified times earlier than the dates inside their text, and 12 rows sit exactly at 2026-08-24T16:41:00-05:00.",
     "Grade document content dates and carrying email timestamps, not filesystem mtime."),
    ("K-07", "Bellweather settlement & $412 correction", "avoid", 1,
     "Exact first-report date or commission rate for Bellweather",
     "$412 correction appears in Stripe in 2025/Apr 2026 before 'first reported'; July 8 paid T+2 vs T+5; 1.0% vs 1.2% commission; QBR/recon dates & edge-case counts conflict.",
     "Scope any Bellweather ask strictly to specific named Stripe payout/charge IDs rather than cross-doc narrative timing."),
    ("K-08", "Cactus Bloom -$4,200 make-good sign", "avoid", 1,
     "Whether Cactus Bloom make-good is a credit or -$4,200 payout",
     "CRM, FinTrack, and QuickBooks record it as a credit, while Stripe has a -$4,200 payout and the statement nets the organizer down.",
     "Avoid grading the sign of the Cactus Bloom make-good across systems without clarifying the exact system of record."),
    ("K-09", "Stripe payouts vs finance statements", "avoid", 1,
     "Reconciling payout registers, 120 dispute notes, or 7 stale subscriptions to Stripe",
     "11 statement payout IDs are missing from Stripe; 120/122 dispute notes have no Stripe dispute; 7 active subscriptions stopped invoicing in Jan 2026; payout register has 4 rows off Stripe.",
     "Build Stripe tasks only on verified live Stripe IDs (`stripe.json`), not unverified statement totals."),
    ("K-10", "Spend approval & $5,000 signature line", "avoid", 1,
     "Who holds the $5,000 approval line or countersigning rule",
     "Two conflicting answers exist on who holds the $5,000 spend approval line, and one Notion note reads as ruling out countersigning.",
     "Settle the specific approval threshold explicitly via injection if your scenario depends on it."),
    ("K-11", "Vendor PDFs (PagerDuty, Datadog, AWS)", "avoid", 1,
     "Vendor PDF seat counts, MSA numbering, or invoice dates",
     "PagerDuty order form & 2025 PDFs print 4 seats/$720/Sep 30 term; Datadog quote refs are stubs; AWS IEM scope and PagerDuty invoice numbers have gaps.",
     "Verify every vendor PDF claim word-for-word against the exact PDF in Universe_Sonorra/Users/."),
    ("K-12", "Cost-centre workbooks vs QuickBooks", "avoid", 1,
     "Matching cost-centre XLSX account names or preclose Micah $4,800 row to QuickBooks",
     "Cost-centre XLSX account names are not QuickBooks accounts; preclose keeps a ghost $4,800 Micah row; BILL-5208 posted on 07-26.",
     "Do not ask the agent to treat cost-centre spreadsheet account labels as QuickBooks chart-of-accounts names."),
    ("K-13", "Personal finance & account 9946", "watch", 0,
     "Assuming housing/insurance expenses or unpaid corporate cards in FinTrack",
     "Yusuf's FinTrack shows ~96% to savings, no housing/insurance, texted spend missing from FinTrack, account 9946 frozen, corporate cards never paid.",
     "Build FinTrack asks only on actual rows in fintrack.json or injected SQL."),
    ("K-14", "Discovery attribution 29.4% timeline", "avoid", 1,
     "When discovery attribution reached 29.4%",
     "Docs from Sep 2025–Jun 2026 state 'Currently 29.4%', while dated captures climb from 24.6% and reach 29.4% only in Aug 2026.",
     "Do not grade historical attribution percentages across Sep 2025–Jun 2026 docs."),
    ("K-16", "Branch protection & SOC 2 evidence", "avoid", 1,
     "Whether sonorra-core branch protection is enabled or bypassed",
     "GitHub shows all 10 repos protected, while a screenshot, an email, and a CSV say sonorra-core is unprotected; Northgate registers lack a bypass column.",
     "Cite the specific source (GitHub export vs Request 04 CSV) or avoid sonorra-core branch-protection claims."),
    ("K-17", "GitHub histories & RFC-014 PR numbers", "watch", 0,
     "Cross-repo RFC-014 revisions or mobile release numbering",
     "RFC-014 revisions sit in wrong repos, two mobile release schemes coexist, and some commit histories are imported.",
     "Verify exact repo/PR/SHAs in github_hg4.json before citing."),
    ("K-18", "Linear duplicate keys & self-keyed titles", "watch", 0,
     "Counting Linear tickets across storyline passes or empty Cycle 39",
     "Storylines duplicate across passes, Cycle 39 is nearly empty, labels vs label_ids disagree in spots, 51 titles retain self-keys.",
     "Anchor Linear asks by exact issue keys (`ENG-...`) rather than broad title/count sweeps."),
    ("K-19", "Derived weeklies & standups vs SOT", "avoid", 1,
     "Treating weekly digests or old standups as current Linear/GCal state",
     "676 of 1,902 weekly entries diverge from GCal; old standups/handoffs print stale owners, titles, or deleted keys.",
     "Grade Linear/GCal directly as system of record rather than un-regenerated weekly summaries."),
    ("K-20", "CRM 'Logged by' & missing engagement owners", "watch", 0,
     "CRM engagement ownership",
     "44 CRM bodies end with 'Logged by...' and engagements lack an owner field.",
     "Do not require inferring CRM engagement owners from missing fields."),
    ("K-21", "Calendar fillers & attendee-copy diffs", "watch", 0,
     "Recurring calendar series or QBR/board attendees",
     "No all-day/rrule events, 294 attendee-copy differences, QBR/board events lack attendees, Monday shown as office day.",
     "Verify attendee presence on the acting persona's actual calendar events."),
    ("K-22", "12 unresolved Linear keys & WSDL mismatch", "avoid", 1,
     "Citing the 12 non-existent Linear keys or BST-341 WSDL operations",
     "12 cited Linear keys do not exist in linear.json; bypass finding has two keys; BST-341 names operations absent from WSDLs.",
     "Confirm every cited Linear key exists in linear.json before including it in prompt/rubric."),
    ("K-24", "universe.yaml departments & footprint counts", "avoid", 1,
     "Using universe.yaml department='engineering' or footprint totals",
     "All 15 personas in universe.yaml list department='engineering'; 18 phone mismatches and 8 Marguerites exist in contacts; footprints are off by 1–12.",
     "Use canonical job titles from roster.json / contacts.json and exact record counts from service exports."),
    ("K-27", "Micah Trent engagement & BILL-9314 timing", "avoid", 1,
     "Micah Trent SOW dates vs BILL-9314 or June SMS",
     "Measurements predate SOW; BILL-9314 precedes the engagement; SOW PDFs omit term; June SMS copy the August exchange.",
     "Keep Micah Trent asks focused on specific invoice/code artifacts rather than engagement timeline reconciliation."),
    ("K-29", "Packaging & current_user / leftover filenames", "watch", 0,
     "Expecting labels.json or universe overview docs in service exports",
     "gmail/labels.json is empty; DMs are stored as named channels; service filenames use legacy suffixes (notion_fixed, github_hg4).",
     "Use Evals/UNIVERSE_MAP.md for canonical filenames."),
    ("V12-C1", "Refund run 3,140 vs Stripe 62 duplicate refunds", "avoid", 1,
     "Expecting 3,140 refund rows in Stripe",
     "Incident docs state 3,140 refunds while Stripe intentionally holds 62 duplicate refunds (cannot mint 3,140 without a second ledger).",
     "Treat 3,140 as the documented incident report figure and 62 as the Stripe export figure."),
    ("V12-C2", "Dispute window 160 vs Stripe 2 disputes", "avoid", 1,
     "Expecting 160 dispute rows in Stripe",
     "Docs state 160 disputes in the window while Stripe intentionally holds 2 disputes.",
     "Distinguish documented dispute-window counts from live Stripe dispute records."),
]


def main() -> int:
    issues = []
    for iid, title, level, fails, dont, why, safer in SONORRA_ISSUES:
        issues.append({
            "id": iid,
            "universe": "sonorra",
            "title": title,
            "level": level,
            "fails": fails,
            "dont": dont,
            "why": why,
            "safer": safer,
        })
    OUT.write_text(json.dumps({"verified": VERIFIED, "issues": issues}, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Known Data Issues (Sonorra v17)",
        "",
        f"Built by deploy/build_known_issues.py from Sonorra_known_issues.md (verified {VERIFIED}).",
        "Base-data records that contradict each other. Do not grade tasks on these contradictions unless",
        "explicitly scoped to a named system of record or settled via `01_Injection.sql`.",
        "",
        "## Sonorra v17 (2026-08-24 16:41 CT)",
        "",
    ]
    for item in issues:
        tag = " [watch]" if item["level"] == "watch" else ""
        lines.append(f"### {item['id']} — {item['title']}{tag}")
        lines.append(f"- **Don't grade:** {item['dont']}")
        lines.append(f"- **Why:** {item['why']}")
        lines.append(f"- **Safer route:** {item['safer']}")
        lines.append("")
    MD_OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(issues)} Sonorra known issues to {OUT.name} and {MD_OUT.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
