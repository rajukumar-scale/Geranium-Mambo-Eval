#!/usr/bin/env python3
"""Build server/known_issues.json, the chat page's "Known data issues" list, from the base-data review files.

    python3 deploy/build_known_issues.py ~/Downloads/known_broken_data_2026-10-30

The folder holds what the 2026-10-30 review of 6,396 portal reports produced (both sides of every issue checked in the
universe exports):
  cb_list_synthcut.md      SC-1..SC-73, one contributor line each: "**SC-n** (N FAILs): Don't build a graded answer on
                           <what>: <side A>, but <side B>. *Safe alternative:* <...>"
  clusters_synthcut.json   per issue: fail_reports, reports, verification (VERIFIED / PARTIAL ...)
  known_base_issues_normal.md + clusters_normal.json   NB-1..NB-33 (cb_list_normal.md has no ids and merges NB-5 with
                           NB-8, so the normal wording lives in NB_TEXT below, taken from that list and the write-ups)

FAIL counts and levels always come from the cluster files; the short titles and plain-language fixes are written here.
A new review: point this at its folder, add titles for new ids (the script stops and names any it lacks), run it,
and commit the JSON. The date in the folder name becomes the sidebar header's date (or pass --verified).
"""

import argparse
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "server" / "known_issues.json"
MAX_TITLE_WORDS = 8

# ---- synth-cut (Oct 1, 2025) ------------------------------------------------------------------------------------
SC_TITLES = {
    "SC-1": "Domino Delights September 2025 installs",
    "SC-2": "CASH_BALANCE burn column comment",
    "SC-3": "Runway: investor letter vs CASH_BALANCE",
    "SC-4": "adjoe payments: ledger vs bank wires",
    "SC-5": "Victor Barnes paid after he left",
    "SC-6": "Node CEO emails from an HG address",
    "SC-7": "People who left channels still listed",
    "SC-8": "ZOM-475 glass crash: fixed or not",
    "SC-9": "Play Ventures: rejected or no reply",
    "SC-10": "LVP meeting date: Sep 7 vs Sep 9",
    "SC-11": "Unity spend is $0 in Snowflake",
    "SC-12": "Meta spend missing from Snowflake",
    "SC-13": "ZM3D beta date in Trello list name",
    "SC-14": "Beta cards' two 'complete' flags disagree",
    "SC-15": "ZOM-521 checklist ticked, children still open",
    "SC-16": "Linear tickets still open after work shipped",
    "SC-17": "Ad-revenue payouts per network don't match",
    "SC-18": "PR #477: open or closed",
    "SC-19": "Telemetry app versions vs Slack builds",
    "SC-20": "Node Slack channel creation date",
    "SC-21": "Jan 2025 Node wire missing from ledger",
    "SC-22": "time_in_status_minutes runs past the clock",
    "SC-23": "AppLovin spend dates and countries",
    "SC-24": "Tim Steudler works for Mattel, not HG",
    "SC-25": "PAYROLL_ROSTER vs who actually works there",
    "SC-26": "Slack links to ENG-2367/2368/2373 don't exist",
    "SC-27": "#god-bugs and #god-gameart archive state",
    "SC-28": "Product OKRs page numbers vs Snowflake",
    "SC-29": "MMP report times before the install",
    "SC-30": "Done parent tickets with open children",
    "SC-31": "Live-ops weekend outage dates and events",
    "SC-32": "'In Review' tickets with completion dates",
    "SC-33": "Investor sheet lists Acterra twice",
    "SC-34": "Investor sheet row 82: Superplay vs Supercell",
    "SC-35": "P&L workbook month headers mislabeled",
    "SC-36": "Victor Barnes still on invites and channels",
    "SC-37": "Crash-by-level comment vs the rows",
    "SC-38": "Season Pass ship date and analytics",
    "SC-39": "Singular plan: quarterly or two installments",
    "SC-40": "July board meeting date: 14 vs 16",
    "SC-41": "ENG-2366 cited before it existed",
    "SC-42": "Node kickoff date: Sept 22 vs 23",
    "SC-43": "Simon's Aug-Sep 2024 pay",
    "SC-44": "Tutorial step events listed but empty",
    "SC-45": "Confluence backend pages vs the code",
    "SC-46": "HEADCOUNT comment says 12, rows say 15",
    "SC-47": "Weekly Quests status and incident timeline",
    "SC-48": "'Pitch Sep 25' deck date",
    "SC-49": "Founder count and Frederick's role",
    "SC-50": "Bot comment authors: Organization vs Bot",
    "SC-51": "Node Google Ads link: linked or pending",
    "SC-52": "Backend PR #79 open but already merged",
    "SC-53": "One player token used for two players",
    "SC-54": "Frederick paid after his last paycheck",
    "SC-55": "Mattel RFP deadline: November 31",
    "SC-56": "Commit title case: GitHub vs #builds",
    "SC-57": "#zm-collect-win archived but still active",
    "SC-58": "ZM3D monster roster and boss rules",
    "SC-59": "Country of early adjoe installs",
    "SC-60": "Josh Dempsey identity mix-up",
    "SC-61": "ENG-2255 owner: Brian or Samuel",
    "SC-62": "Reward import: hourly or every 5 minutes",
    "SC-63": "Griffin pro-rata range math",
    "SC-64": "Node Media's two addresses",
    "SC-65": "ZM average playtime: 18 vs 37 min",
    "SC-66": "Closed analytics AWS account still active",
    "SC-67": "Build #1621 post lists #1620",
    "SC-68": "Boss Arena on prod before its code",
    "SC-69": "April 2025 BigQuery cost: 123.47 vs 126",
    "SC-70": "Slack timezones all Los Angeles",
    "SC-71": "ZOM-471 priority: high vs Medium",
    "SC-72": "Python 3.9 end-of-life date in the future",
    "SC-73": "Monthly burn in pitch text",
}

# Where the contributor line is garbled, uses jargon or doesn't name the record: replacement dont / why / safer.
SC_FIX = {
    "SC-3": {"dont": "A runway figure from the investor letter or CASH_BALANCE."},
    "SC-4": {"safer": "Only the 3-month total is safe (both about $46,201.50)."},
    "SC-5": {"dont": "Sep 2025 payroll, headcount, burn or runway from FINANCE_LEDGER or PAYROLL_ROSTER."},
    "SC-9": {"dont": "Play Ventures' status in the investor sheet or Slack."},
    "SC-10": {"dont": "The LVP meeting date in the investor sheet."},
    "SC-11": {"why": "Every unity_ads row has spend 0 and CPI 0, but Slack shows paid Unity campaigns ($5-6 bids, "
                     "$300/day caps); Confluence admits the data feed misses Unity spend."},
    "SC-14": {"dont": "Which Trello beta cards are 'complete' (cards 54/56/58/67).",
              "why": "The Trello card's dueComplete is false, but its badges.dueComplete is true."},
    "SC-19": {"dont": "Matching RAW_EVENTS app versions to a store build in Slack."},
    "SC-22": {"dont": "Ticket durations from Linear's time_in_status_minutes."},
    "SC-24": {"dont": "Tim Steudler's employer (he has an HG email and HG accounts)."},
    "SC-29": {"dont": "MMP reporting latency from INSTALL_ATTRIBUTION.",
              "why": "INSTALL_ATTRIBUTION has mmp_reported_at earlier than install_ts on 12,730 of 28,609 rows, so the "
                     "MMP reports installs before they happen."},
    "SC-31": {"safer": "Measure only from the degraded rows in LIVE_EVENT_PERFORMANCE."},
    "SC-39": {"dont": "The Singular payment plan from the Aug 28 exec sync."},
    "SC-46": {"dont": "Total people at Sep 2025 from the HEADCOUNT table comment."},
    "SC-53": {"why": "#live-incidents uses the token for 'another player', but it is the same token as the earlier "
                     "player, so the hidden names merged two people."},
    "SC-54": {"dont": "Frederick's pay after May 2025 in the ledger."},
    "SC-55": {"dont": "An exact deadline from the Mattel RFP."},
    "SC-61": {"dont": "Who owns Linear ENG-2255."},
    "SC-63": {"dont": "A Griffin pro-rata dollar figure from the Aug 28 sync."},
    "SC-64": {"dont": "Node Media's address on its invoices."},
    "SC-67": {"dont": "Which build contained PR #439, from the #builds post."},
    "SC-70": {"dont": "A person's timezone from Slack."},
    "SC-71": {"dont": "ZOM-471's priority from the Linear-bot post."},
}

# ---- normal (Feb 14, 2026) --------------------------------------------------------------------------------------
NB_TEXT = {
    "NB-1": {"title": "Slack channel access after Feb 11",
             "dont": "Who can read a Slack channel after 2026-02-11 20:40 UTC, or injected posts by someone in a "
                     "channel they left.",
             "why": "Julia, Marcus, Owen, Oliver, Martin, Claire and Brian leave their channels on Feb 11 and Leonard "
                    "makes them single-channel guests, but channels.json still lists them as members.",
             "safer": "Use #prototype, their DMs, or anything before Feb 11."},
    "NB-2": {"title": "P&L forecast sheet costs vs Snowflake",
             "dont": "Monthly AWS, hosting, Unity licence, UA or salary costs from the \"P&L and forecast - Jan 2026\" "
                     "sheet.",
             "why": "The sheet shows AWS = 0 and Jan salaries of $17,000, but Snowflake has AWS at $1,485–$2,745 every "
                    "month, Jan salaries of $9,895.88 and all SaaS at $786.",
             "safer": "Use the vendor's own invoice or receipt."},
    "NB-3": {"title": "Cash at Dec 31 and Jan 31",
             "dont": "Cash on Dec 31, 2025 or Jan 31, 2026 from CASH_BALANCE, Leonard's email or the P&L sheet.",
             "why": "CASH_BALANCE says $25,031 for Dec 31, but Leonard's email and the P&L sheet say about $52,686. "
                    "For Jan 31 Snowflake says $8,096 and the sheet $1,653.84."},
    "NB-4": {"title": "February 2026 payments: paid or only accrued",
             "dont": "Whether a February 2026 payment was made (Sunset $11,700, legal $780, AWS and others), from "
                     "FINANCE_LEDGER.",
             "why": "The ledger books them as cash out, but MONTHLY_BURN says February was only accrued and never "
                    "closed, and Sunset's own email says its payment is still due."},
    "NB-5": {"title": "Helpshift effective date: February 29, 2025",
             "dont": "Helpshift's effective cancellation date from its acknowledgement email, or asks to \"quote the "
                     "vendor's exact date\".",
             "why": "Helpshift's reply says \"effective February 29,2025\", a date that doesn't exist and is in the "
                    "wrong year; Leonard had asked to stop at the end of the current billing cycle in Feb 2026.",
             "safer": "Grade only that Helpshift agreed to cancel."},
    "NB-6": {"title": "Slack messages after Feb 13 10:03 UTC",
             "dont": "Slack messages from Feb 13 after 10:03 UTC.",
             "why": "The Slack export stops at 10:03 UTC on Feb 13, but two threads (BoomBit in C09SWTHEMEW, the "
                    "#prototype \"common rarity\" thread) and three file uploads point to later messages that don't "
                    "exist."},
    "NB-7": {"title": "Combo Fighter levels past 35",
             "dont": "How far Combo Fighter players got, or level funnels, from the warehouse.",
             "why": "The warehouse has hundreds of level 36–438 events from Dec 28 on, but the GDD and Robert (Feb 2) "
                    "say players can't go past level 35."},
    "NB-8": {"title": "Helpshift payments left: $150×10 or ×8",
             "dont": "How many Helpshift payments are left, or \"Leonard's latest count\", from #winddown or "
                     "#executives.",
             "why": "On Feb 9 in #winddown Leonard says \"we owe Helpshift $150*10 = $1500\", but on Feb 10 in "
                    "#executives he writes \"$150*8 months\".",
             "safer": "Grade only that Helpshift agreed to cancel."},
    "NB-9": {"title": "Combo Fighter on Google Play",
             "dont": "Whether Combo Fighter is on Google Play, or its Android players.",
             "why": "Robert says \"we aren't official on google\", but Snowflake has 206 Combo Fighter Android players "
                    "and Play Store reviews."},
    "NB-10": {"title": "CASH_BALANCE burn column comment",
              "dont": "The CASH_BALANCE.monthly_net_burn column comment (October 2025 payroll and headcount, \"the UA "
                      "row omits wires\").",
              "why": "The comment says October 2025 payroll was $32,172 for 9 people and the UA row misses $46,201.50, "
                     "but the data shows $39,292.02 for 13 people and the adjoe wires are already counted."},
    "NB-11": {"title": "Combo Fighter GDD is out of date",
              "dont": "Combo Fighter ads, monetization or \"parked\" status taken from the Combo Fighter GDD in "
                      "Confluence.",
              "why": "The GDD says the game \"remains parked\" and ads are \"outside MVP scope\", but Slack shows "
                     "version 1.0.91 with in-app purchases (Feb 6) and interstitial ads turned on (Feb 12)."},
    "NB-12": {"title": "Vendor names hidden behind tokens",
              "dont": "A vendor or tool that a record names only as [PERSON_NAME_…] or [USERNAME_…], such as "
                      "Bizxpress's subject line or the Helpshift row in the Task List.",
              "why": "Records like \"cursor and [PERSON_NAME_20852]\" replace the vendor's name with a token while "
                     "other records spell it out, so nobody can tell they mean the same vendor.",
              "safer": "Use a record that spells out the name."},
    "NB-13": {"title": "Exact GitHub branch names",
              "dont": "An exact GitHub branch name.",
              "why": "The branches list says marcus.bennett/…, but PRs and Slack say Marcus/….",
              "safer": "If you need one, accept both forms."},
    "NB-14": {"title": "ENG-2403/ENG-2404 checklists vs child tickets",
              "dont": "The ENG-2403 and ENG-2404 checklists in Linear.",
              "why": "They tick items whose tickets are still in PR or Validation, and leave Done items unticked.",
              "safer": "Use the child tickets' own states."},
    "NB-15": {"title": "Employee or contractor",
              "dont": "Whether someone was an employee or a contractor, from PAYROLL_ROSTER.",
              "why": "PAYROLL_ROSTER marks most people full_time, but Leonard says \"no US employees, only "
                     "international contractors\" and the termination letters say \"independent contractor\"."},
    "NB-16": {"title": "Domino Delights database: Aurora or DynamoDB",
              "dont": "Which database Domino Delights runs on, from Confluence.",
              "why": "One Confluence page says Aurora PostgreSQL is the primary datastore; another says Domino "
                     "Delights runs on DynamoDB."},
    "NB-17": {"title": "Domino Delights installs in Jan–Feb 2026",
              "dont": "Domino Delights paid, Brazil or worldwide installs in Jan–Feb 2026, from the warehouse.",
              "why": "BoomBit says the Brazil test went live, but Snowflake has only organic installs and zero Brazil "
                     "installs ever."},
    "NB-18": {"title": "Domino Delights September 2025 installs",
              "dont": "Domino Delights installs in September 2025, or adjoe credits for that month.",
              "why": "Slack says about 450 installs and adjoe turned on Sep 1, but Snowflake has zero Domino Delights "
                     "installs that month."},
    "NB-19": {"title": "Unity Ads spend is $0 in the warehouse",
              "dont": "Unity Ads dollar amounts from AD_SPEND_DAILY.",
              "why": "AD_SPEND_DAILY shows $0 on every Unity row, but Gmail has Unity Ads receipts and a $1,000 "
                     "prepayment."},
    "NB-20": {"title": "Board marketing figure: $150K or $170K",
              "dont": "What marketing figure the Board was told.",
              "why": "The board email body says about $150K, but the attached deck and the Slack drafts say $170K."},
    "NB-21": {"title": "Review app versions vs Slack builds",
              "dont": "Joining review or warehouse app versions to Slack build numbers.",
              "why": "ZM3D reviews say 0.1.601/0.1.688 while Slack says 0.3.x, and Domino Delights reviews say "
                     "0.3.104 while Slack says 0.3.305."},
    "NB-22": {"title": "Contractor letter 00016874 termination date",
              "dont": "A termination date from contractor letter 00016874.",
              "why": "It says January 31, 2025, but the sibling letters sent minutes later say January 31, 2026."},
    "NB-23": {"title": "Singular invoice INVSINC22671 has two totals",
              "dont": "A Singular \"as billed\" total from invoice INVSINC22671.",
              "why": "The invoice exists twice with the same date: once for $25,000 and once for $6,250."},
    "NB-24": {"title": "Linear has no comments or links",
              "dont": "What a base Linear comment said, or missing ticket links.",
              "why": "The Linear export has zero comments and zero links, but Slack and Gmail refer to comments on "
                     "tickets."},
    "NB-25": {"title": "Domino Delights store history in 2024 roadmap",
              "dont": "Domino Delights' store history from the 2024 roadmap page in Confluence.",
              "why": "The page says Domino Delights shipped as \"Puzzles vs Zombies\", but that was Zombie Match 3D."},
    "NB-26": {"title": "Combo Fighter sales: none or two",
              "dont": "Whether Combo Fighter sold anything.",
              "why": "The QA runbook says no purchase was ever recorded, but Robert says \"2 people bought rotation "
                     "pack\"."},
    "NB-27": {"title": "Quest auto-deploy: on or off",
              "dont": "Whether the quest auto-deploy is on, from the Quest System postmortem.",
              "why": "The postmortem body says the 8-hour auto-deploy \"was left running\", but its page comment says "
                     "auto-deploy \"stays off\" until fixes ship."},
    "NB-28": {"title": "ZM3D Meta campaign end date",
              "dont": "The end date of the ZM3D Meta campaign.",
              "why": "Leonard turned it off on Nov 3 at 03:13 UTC, but Snowflake still has $246.18 of spend dated "
                     "Nov 3."},
    "NB-29": {"title": "Shutdown decision date: Feb 9 or 10",
              "dont": "The shutdown decision date as the start of a time window.",
              "why": "On Feb 9 in #executives Leonard says \"we finalized the decision to shut down\", but on Feb 11 "
                     "in #leapblock he says \"We didn't finalize this decision until yesterday\"."},
    "NB-30": {"title": "Endless Monster Mode: live or removed",
              "dont": "Whether Endless Monster Mode (or Infinite Boss mode) is live, from the Live-Ops calendar.",
              "why": "The Live-Ops calendar says it is live, but Leonard said game modes are being removed (ZOM-642, "
                     "still in Validation)."},
    "NB-31": {"title": "Slack DM member lists",
              "dont": "Who had access, argued from Slack DM or group-DM member lists.",
              "why": "Most 1:1 DMs list 3–5 members, and many group-DM names don't match their members."},
    "NB-32": {"title": "Douglas's end date: October or December",
              "dont": "Douglas's end date.",
              "why": "The AWS Infrastructure Guide says he departed in Oct 2025, but the ledger pays him through "
                     "Dec 31, 2025."},
    "NB-33": {"title": "December DAU drop from 70 to 24",
              "dont": "Leonard's \"DAU dropped from 70 to 24\".",
              "why": "Leonard says DAU dropped from 70 to 24, but DAILY_ACTIVE_USERS shows 68 and 64 for Dec 26 and "
                     "27."},
}

SC_LINE = re.compile(r"^- \*\*(SC-\d+)\*\* \((\d+) FAILs?\): Don't build a graded answer on (.+)$", re.M)
KEEP_LOWER = {"adjoe"}  # brand names that start lowercase


def sentence(text):
    """Capitalize a plain first word (not 'channels.json', 'time_in_status_minutes' or 'adjoe') and end with a full stop."""
    text = text.strip()
    first = text.split(" ", 1)[0]
    if re.fullmatch(r"[a-z][a-z'/-]*", first) and first not in KEEP_LOWER:
        text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def watch_level(partial, reports):
    """'watch' = only partly confirmed, or seen in a single report. Everything else is 'avoid', soft issues included:
    the source says a soft issue (an in-world explanation exists) is still a trap, so don't grade on it."""
    return "watch" if partial or reports <= 1 else "avoid"


def synthcut(src):
    clusters = {c["id"]: c for c in json.loads((src / "clusters_synthcut.json").read_text())
                if re.fullmatch(r"SC-\d+", c.get("id") or "")}
    out = []
    for sid, fails, rest in SC_LINE.findall((src / "cb_list_synthcut.md").read_text()):
        rest, _, safer = rest.partition(" *Safe alternative:* ")
        dont, sep, why = rest.partition(": ")
        if not sep:
            sys.exit(f"{sid}: can't split '<what>: <why>' in the contributor line")
        c = clusters.get(sid) or sys.exit(f"{sid} is in cb_list_synthcut.md but not in clusters_synthcut.json")
        if int(fails) != c["fail_reports"]:
            sys.exit(f"{sid}: the list says {fails} FAILs, the clusters file {c['fail_reports']}")
        if sid not in SC_TITLES:
            sys.exit(f"{sid} has no title: add one to SC_TITLES")
        item = {"id": sid, "universe": "synthcut", "title": SC_TITLES[sid], "dont": sentence(dont), "why": sentence(why),
                "safer": sentence(safer) if safer.strip() else None, "fails": c["fail_reports"],
                "level": watch_level(c.get("verification") == "PARTIAL", c.get("reports", 0))}
        item.update(SC_FIX.get(sid, {}))
        out.append(item)
    missing = sorted(set(clusters) - {i["id"] for i in out}, key=lambda s: int(s[3:]))
    if missing:
        sys.exit(f"in clusters_synthcut.json but not in cb_list_synthcut.md: {', '.join(missing)}")
    return out


def normal(src):
    clusters = {c["nb_id"]: c for c in json.loads((src / "clusters_normal.json").read_text())["clusters"] if c.get("nb_id")}
    headers = set(re.findall(r"^### (NB-\d+) ", (src / "known_base_issues_normal.md").read_text(), re.M))
    if set(clusters) != headers or set(clusters) != set(NB_TEXT):
        sys.exit(f"NB ids differ: clusters {sorted(set(clusters) ^ headers)} vs write-up, "
                 f"{sorted(set(clusters) ^ set(NB_TEXT))} vs NB_TEXT")
    out = []
    for nid, c in clusters.items():
        status = c.get("status") or ""  # e.g. "VERIFIED", "VERIFIED (soft)", "VERIFIED (soft, single)"
        reports = 1 if "single" in status else c.get("reports", 0)
        out.append({"id": nid, "universe": "normal", **NB_TEXT[nid], "fails": c["fail_reports"],
                    "level": watch_level("partial" in status.lower(), reports)})
    return out


def check(issues):
    ids = [i["id"] for i in issues]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for i in issues:
        for key in ("title", "dont", "why"):
            assert i.get(key), f"{i['id']}: no {key}"
        assert len(i["title"].split()) <= MAX_TITLE_WORDS, f"{i['id']}: title over {MAX_TITLE_WORDS} words: {i['title']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("src", type=Path, help="folder with cb_list_*.md, clusters_*.json, known_base_issues_*.md")
    parser.add_argument("--verified", help="date the list was checked (default: the date in the folder name)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    verified = args.verified or (re.search(r"\d{4}-\d{2}-\d{2}", args.src.name) or [None])[0]
    if not verified:
        sys.exit("No date in the folder name: pass --verified YYYY-MM-DD")
    issues = []
    for group in (synthcut(args.src), normal(args.src)):  # most FAILs first within each universe
        issues += sorted(group, key=lambda i: (-i["fails"], int(i["id"].split("-")[1])))
    issues = [{k: v for k, v in i.items() if v is not None} for i in issues]
    order = ("id", "universe", "level", "fails", "title", "dont", "why", "safer")
    issues = [{k: i[k] for k in order if k in i} for i in issues]
    check(issues)
    data = {"verified": verified,
            "about": "Base-data contradictions found in portal reports and checked on both sides in the universe exports. "
                     "Built by deploy/build_known_issues.py; edit there, not here.",
            "issues": issues}
    args.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    counts = {u: sum(i["universe"] == u for i in issues) for u in ("synthcut", "normal")}
    watch = sum(i["level"] == "watch" for i in issues)
    print(f"wrote {args.out}: {counts['synthcut']} synth-cut + {counts['normal']} normal issues ({watch} watch)")


if __name__ == "__main__":
    main()
