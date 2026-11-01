# Known data issues

Base-data records in the HarmonyGames universes that contradict each other, checked on both sides in the universe exports (checked 2026-10-30). A task graded on one of these usually fails Cross-Service Coherence: the agent can't tell which side is right, so no answer clearly wins. They are the same ids as the chat page's "Known data issues" sidebar, so the contributor can look them up there.

- **avoid**: confirmed. Don't build a graded answer, an OE or a rubric row on it, and never use it as the trap.
- **watch**: only partly confirmed, or seen in a single report. Check both sides before relying on it.

Built by deploy/build_known_issues.py together with server/known_issues.json; edit the script, not this file.

## Task date Oct 1, 2025 (synth-cut): 73 issues

### SC-4 · adjoe payments: ledger vs bank wires (avoid, caused 36 FAILs)
- Don't grade: adjoe payment amounts or dates from FINANCE_LEDGER.
- Why: The ledger books 10,733.60 / 20,537.92 / 14,930.02 on Apr 30 / May 31 / Jun 30, but SVB wires paid 12,005 / 18,890 / 15,306.50 on May 5 / Jul 3 / Aug 29 (the ledger comment even claims 'signed cash movements').
- Safer: Only the 3-month total is safe (both about $46,201.50).

### SC-5 · Victor Barnes paid after he left (avoid, caused 26 FAILs)
- Don't grade: Sep 2025 payroll, headcount, burn or runway from FINANCE_LEDGER or PAYROLL_ROSTER.
- Why: FINANCE_LEDGER/PAYROLL_ROSTER pay Victor Barnes through Sep 2025, but Slack says he left April 16, 2025.

### SC-6 · Node CEO emails from an HG address (avoid, caused 21 FAILs)
- Don't grade: Who sent a Node Media email.
- Why: Node's CEO 'Brian Cooper' sends from brian.foster@harmonygames.co, but that address belongs to HG engineer Brian Foster.
- Safer: Refer to 'Node's CEO' by role.

### SC-1 · Domino Delights September 2025 installs (avoid, caused 19 FAILs)
- Don't grade: Domino Delights installs in September 2025.
- Why: adjoe Slack (Sep 1-8) says adjoe drove an install spike, but INSTALL_EVENTS/DAU have zero DD installs in September.
- Safer: Use Zombie Match 3D September installs, or DD before June.

### SC-2 · CASH_BALANCE burn column comment (avoid, caused 19 FAILs)
- Don't grade: The CASH_BALANCE.monthly_net_burn column comment.
- Why: The comment says the UA row omits $46,201.50 and leaves a ~$47K residual, but MONTHLY_BURN/FINANCE_LEDGER already hold the adjoe money and reconcile to within $0.49.
- Safer: Use the table rows, never the comment.

### SC-7 · People who left channels still listed (watch, caused 18 FAILs)
- Don't grade: Channel membership (who is in a channel).
- Why: #adjoe logs Frederick leaving on 2024-10-31, but he keeps posting there through Sep 2025 and channels.json still lists him (all leavers stay listed).

### SC-8 · ZOM-475 glass crash: fixed or not (avoid, caused 17 FAILs)
- Don't grade: Whether the ZOM-475 glass crash is fixed.
- Why: The ZM3D GDD (Sep 30) says it was fixed by removing the glass FX, but Linear ZOM-475 is still Todo, Trello still lists it, and PR #435 re-added glass FX.

### SC-9 · Play Ventures: rejected or no reply (avoid, caused 17 FAILs)
- Don't grade: Play Ventures' status in the investor sheet or Slack.
- Why: The investor sheet says 'Rejected' with a decline quote (Sep 15), but Leonard says on Sep 23/25 they 'didn't respond'.

### SC-10 · LVP meeting date: Sep 7 vs Sep 9 (avoid, caused 16 FAILs)
- Don't grade: The LVP meeting date in the investor sheet.
- Why: The investor sheet says 'Catch up on 09/07' (a Sunday), but calendar, invite and Slack say Tue Sep 9.
- Safer: If a date is needed, accept Sep 9.

### SC-11 · Unity spend is $0 in Snowflake (avoid, caused 14 FAILs)
- Don't grade: Unity spend, CPI or total UA spend from Snowflake.
- Why: Every unity_ads row has spend 0 and CPI 0, but Slack shows paid Unity campaigns ($5-6 bids, $300/day caps); Confluence admits the data feed misses Unity spend.
- Safer: Use install counts, not spend.

### SC-12 · Meta spend missing from Snowflake (avoid, caused 14 FAILs)
- Don't grade: Meta/Facebook spend or ZM3D paid UA spend.
- Why: Snowflake has no Meta rows after Apr 10, 2025 and none ever for ZM3D, but Gmail Meta receipts bill ZM_UK campaigns ($900 in July) and Slack cites FB spend in September.

### SC-13 · ZM3D beta date in Trello list name (avoid, caused 13 FAILs)
- Don't grade: The ZM3D beta date from the Trello list name.
- Why: The list is named 'BETA: Oct 10', but its last rename action (Sep 12) and every later action say 'BETA: Sep 30'.
- Safer: Settle the date in your own task data.

### SC-14 · Beta cards' two 'complete' flags disagree (avoid, caused 12 FAILs)
- Don't grade: Which Trello beta cards are 'complete' (cards 54/56/58/67).
- Why: The Trello card's dueComplete is false, but its badges.dueComplete is true.

### SC-15 · ZOM-521 checklist ticked, children still open (avoid, caused 11 FAILs)
- Don't grade: What is left on the ZOM-521 VFX master ticket.
- Why: The ZOM-521 checklist ticks all six items, but children ZOM-526 and ZOM-528 are still In Progress.
- Safer: Use the child ticket states.

### SC-16 · Linear tickets still open after work shipped (avoid, caused 11 FAILs)
- Don't grade: Linear status as proof of whether work shipped (ZOM-564, ZOM-513/514, ZOM-563, ART-790/792).
- Why: These tickets are still Todo/In Progress, but their PRs merged or the fix was confirmed in DMs/Slack.

### SC-17 · Ad-revenue payouts per network don't match (watch, caused 11 FAILs)
- Don't grade: Ad-revenue payouts per network.
- Why: AD_REVENUE marks AppLovin/Meta revenue 'paid' (and the ledger books it all as 'AppLovin/MAX'), but Tipalti payout mails and the Meta statement show different amounts.

### SC-18 · PR #477: open or closed (avoid, caused 10 FAILs)
- Don't grade: PR #477's state.
- Why: GitHub says #477 is open, but its CodeRabbit comment says 'The pull request is closed' and cites commits not in the export.

### SC-19 · Telemetry app versions vs Slack builds (avoid, caused 10 FAILs)
- Don't grade: Matching RAW_EVENTS app versions to a store build in Slack.
- Why: RAW_EVENTS uses 0.1.534/0.1.601, but Slack releases are 0.3.x (and also called 1.3.x).
- Safer: Match by date: 0.1.601 starts Sep 30 00:47 UTC.

### SC-20 · Node Slack channel creation date (avoid, caused 10 FAILs)
- Don't grade: When the Node Slack channel started.
- Why: channels.json says it was created 2025-04-09, but its messages start Sep 2024.
- Safer: Date the relationship from messages/email.

### SC-21 · Jan 2025 Node wire missing from ledger (watch, caused 9 FAILs)
- Don't grade: Cash or burn totals that must include the Jan 2025 Node payment.
- Why: SVB confirms a $16,428.57 wire to Node on Jan 2, 2025, but no FINANCE_LEDGER row can hold it and CASH_BALANCE ties to the ledger.

### SC-22 · time_in_status_minutes runs past the clock (avoid, caused 8 FAILs)
- Don't grade: Ticket durations from Linear's time_in_status_minutes.
- Why: The field runs to 2026-02-13, but the clock is Oct 1, 2025.
- Safer: Compute from created_at/completed_at.

### SC-23 · AppLovin spend dates and countries (avoid, caused 8 FAILs)
- Don't grade: AppLovin spend, geos or campaign dates from Snowflake.
- Why: Snowflake books DD AppLovin spend from Apr 11, 2025, multi-country, incl. paused days, but Slack says the US-only campaign first went live Jun 23.

### SC-24 · Tim Steudler works for Mattel, not HG (avoid, caused 8 FAILs)
- Don't grade: Tim Steudler's employer (he has an HG email and HG accounts).
- Why: He writes from tim.steudler@harmonygames.co and has HG accounts, but he is Mattel's BD manager.
- Safer: Call him 'Mattel's contact'; don't count him as staff.

### SC-25 · PAYROLL_ROSTER vs who actually works there (watch, caused 8 FAILs)
- Don't grade: Who is on payroll or current staff from PAYROLL_ROSTER.
- Why: The roster pays Felix in Sep 2025 and lists Calvin as 'General Counsel' for April only, but Slack shows Felix left Aug 2024 and Calvin is the active VFX engineer (also Samuel off-roster).

### SC-3 · Runway: investor letter vs CASH_BALANCE (avoid, caused 7 FAILs)
- Don't grade: A runway figure from the investor letter or CASH_BALANCE.
- Why: The Oct 1 investor letter says ~4 months, but CASH_BALANCE says 2.7 months (Sep 30).

### SC-26 · Slack links to ENG-2367/2368/2373 don't exist (avoid, caused 7 FAILs)
- Don't grade: Resolving Slack's ENG-2367/2368/2373 links.
- Why: Slack links ENG-2368 etc., but Linear stops at ENG-2366; those tickets are ZOM-436/521/527.
- Safer: Use the ZOM ids; never inject new ENG-2367+ issues.

### SC-27 · #god-bugs and #god-gameart archive state (avoid, caused 7 FAILs)
- Don't grade: Posting in #god-bugs or #god-gameart.
- Why: Each has an 'archived the channel' event (Jul 15), but channels.json says is_archived false.
- Safer: Put deciding messages elsewhere.

### SC-28 · Product OKRs page numbers vs Snowflake (avoid, caused 6 FAILs)
- Don't grade: The Confluence Product OKRs numbers.
- Why: It says D1 27.9% on 408 Aug UK iOS installs, a US-only launch and Facebook volume, but Snowflake has 711 installs at ~35.7% D1, a multi-country launch and no ZM3D Meta rows.

### SC-29 · MMP report times before the install (avoid, caused 5 FAILs)
- Don't grade: MMP reporting latency from INSTALL_ATTRIBUTION.
- Why: INSTALL_ATTRIBUTION has mmp_reported_at earlier than install_ts on 12,730 of 28,609 rows, so the MMP reports installs before they happen.

### SC-30 · Done parent tickets with open children (avoid, caused 4 FAILs)
- Don't grade: A parent ticket's status implying its children's.
- Why: Parents like ENG-2072 / ZOM-457 are Done, but their children are still open.
- Safer: Grade each child on its own state.

### SC-31 · Live-ops weekend outage dates and events (watch, caused 4 FAILs)
- Don't grade: The live-ops weekend outage end date or affected events.
- Why: Confluence pages say 'no recorded end', River Rush missed weekends, Streak Fever shipped April, but LIVE_EVENT_PERFORMANCE shows recovery by ~Jul 25, River Rush Mon/Wed/Fri only, Streak Fever runs from Mar 1.
- Safer: Measure only from the degraded rows in LIVE_EVENT_PERFORMANCE.

### SC-32 · 'In Review' tickets with completion dates (avoid, caused 4 FAILs)
- Don't grade: 'In Review' tickets as open or done.
- Why: Their state is In Review, but all 12 carry completed_at.

### SC-33 · Investor sheet lists Acterra twice (watch, caused 4 FAILs)
- Don't grade: Counts of firms in the investor sheet.
- Why: Acterra is listed twice (No. 71 and 86), but it is one firm (also Treo vs Treeo spelling).

### SC-34 · Investor sheet row 82: Superplay vs Supercell (avoid, caused 4 FAILs)
- Don't grade: Investor sheet row No. 82.
- Why: The row is labeled 'Superplay', but its contents are Leonard's Supercell outreach.

### SC-35 · P&L workbook month headers mislabeled (avoid, caused 4 FAILs)
- Don't grade: Month columns in the P&L workbooks.
- Why: Salary/Costs tab headers carry wrong years (2025 columns read 2024), but the Cash P&L tab labels the same columns correctly; Gross Margin and Ending Balance formulas are also broken.
- Safer: Pick columns by position or the Cash P&L tab.

### SC-36 · Victor Barnes still on invites and channels (avoid, caused 4 FAILs)
- Don't grade: Invite/share lists built from channel or calendar membership.
- Why: Victor Barnes is still in #god-gameart, recurring invites and owns EPI-4, but he left in April 2025.

### SC-37 · Crash-by-level comment vs the rows (watch, caused 4 FAILs)
- Don't grade: A ZM3D crash-by-level split.
- Why: The CLIENT_ERROR_EVENTS comment says ~82% of crashes are at levels 3-5, but the rows put ~11% there.

### SC-38 · Season Pass ship date and analytics (watch, caused 3 FAILs)
- Don't grade: Season Pass ship date or analytics status.
- Why: The spec says production Sep 22-29 and events are tagged, but PR #438 shipped in builds before that window and ZOM-552 analytics is still In Progress.
- Safer: Avoid ZOM-529 (it only exists in injected SQL).

### SC-39 · Singular plan: quarterly or two installments (avoid, caused 3 FAILs)
- Don't grade: The Singular payment plan from the Aug 28 exec sync.
- Why: The Aug 28 exec sync says 2 x $12.5K, but Singular's email, Slack and the Sep 26 $6,250 wire say quarterly.

### SC-40 · July board meeting date: 14 vs 16 (avoid, caused 3 FAILs)
- Don't grade: The July 2025 board meeting date from the email body.
- Why: The body says 'July 14, Wednesday', but subject and calendar say Wednesday July 16.
- Safer: Accept July 16.

### SC-41 · ENG-2366 cited before it existed (avoid, caused 3 FAILs)
- Don't grade: When ENG-2366 was documented.
- Why: The Release Checklist (Jul 26 08:50Z) cites ENG-2366, but ENG-2366 was created 13 hours later.
- Safer: Its content is fine to use.

### SC-42 · Node kickoff date: Sept 22 vs 23 (avoid, caused 3 FAILs)
- Don't grade: The Node kickoff date.
- Why: Node's invoice note says the kickoff was Sept 22 (a Sunday), but the calendar has it Mon Sept 23, 2024.
- Safer: Use the invoiced $1,428.57 as stated.

### SC-43 · Simon's Aug-Sep 2024 pay (watch, caused 3 FAILs)
- Don't grade: What Simon was owed/paid for Aug-Sep 2024.
- Why: The P&L shows $0, but Slack shows 10 August hours entered in Deel; Simon has no PAYROLL_ROSTER row.

### SC-44 · Tutorial step events listed but empty (avoid, caused 3 FAILs)
- Don't grade: A tutorial funnel from Snowflake.
- Why: The SESSION_LEVEL_EVENTS comment lists tutorial_step events, but there are 0 such rows.

### SC-45 · Confluence backend pages vs the code (watch, caused 3 FAILs)
- Don't grade: Backend deploy/migration/health-check details from Confluence.
- Why: The pages say migrations don't run on boot, /healthz, check.yml existed in March, but the repos run migrations on boot, use /health, and check.yml came later.
- Safer: Read the code instead.

### SC-46 · HEADCOUNT comment says 12, rows say 15 (avoid, caused 3 FAILs)
- Don't grade: Total people at Sep 2025 from the HEADCOUNT table comment.
- Why: The HEADCOUNT comment says ALL PEOPLE is 12, but 15 rows are active (12 + 3 founders).

### SC-47 · Weekly Quests status and incident timeline (avoid, caused 3 FAILs)
- Don't grade: Weekly Quests status or the Nov 2024 quest incident timeline.
- Why: The live-ops calendar still says 'Failed Nov 2024' and the postmortem dates the fix Nov 18, but the playbook says fixed, Slack shows the fix on Nov 15, and code types differ.

### SC-48 · 'Pitch Sep 25' deck date (avoid, caused 3 FAILs)
- Don't grade: The date of the 'Pitch Sep 25' deck.
- Why: Drive says it was created 2025-07-29, but it contains the $2.5M ask and AUG 25 metrics from late August.
- Safer: Date the ask from the Aug 28 sync.

### SC-49 · Founder count and Frederick's role (avoid, caused 2 FAILs)
- Don't grade: Founder counts or Frederick's founder status.
- Why: The persona roster lists 4 co-founders incl. Frederick, but Snowflake has 3 founders and books Frederick as non-founder 'Commercial Lead'.

### SC-50 · Bot comment authors: Organization vs Bot (avoid, caused 2 FAILs)
- Don't grade: Which GitHub account posted a bot comment.
- Why: Comments are by 'Organization' users, but matching timeline events are by separate 'Bot' users.
- Safer: Refer to 'CodeRabbit' by content.

### SC-51 · Node Google Ads link: linked or pending (avoid, caused 2 FAILs)
- Don't grade: The Node Google Ads link status.
- Why: An 08:04 email says the account 'is now linked', but an 08:06 email says the link request is 'pending'.

### SC-52 · Backend PR #79 open but already merged (avoid, caused 2 FAILs)
- Don't grade: Whether game-of-dominoes-backend PR #79 shipped.
- Why: The PR is open and unmerged, but its head commit is already main's head.

### SC-53 · One player token used for two players (watch, caused 2 FAILs)
- Don't grade: Player identity or counts from [IDENTIFIER_9782].
- Why: #live-incidents uses the token for 'another player', but it is the same token as the earlier player, so the hidden names merged two people.

### SC-54 · Frederick paid after his last paycheck (avoid, caused 2 FAILs)
- Don't grade: Frederick's pay after May 2025 in the ledger.
- Why: Leonard says May 2025 is his last paycheck (then $1), but the ledger pays him $3.3-3.9K a month through Sep.

### SC-55 · Mattel RFP deadline: November 31 (avoid, caused 2 FAILs)
- Don't grade: An exact deadline from the Mattel RFP.
- Why: The RFP says 'November 31, 2025', but that date doesn't exist.
- Safer: Accept 'end of November 2025'.

### SC-56 · Commit title case: GitHub vs #builds (avoid, caused 2 FAILs)
- Don't grade: Exact-case commit title matches between GitHub and #builds.
- Why: #builds shows titles in one case, but GitHub has different case/apostrophes (~478 lines).
- Safer: Match by SHA/PR number.

### SC-57 · #zm-collect-win archived but still active (watch, caused 1 FAIL)
- Don't grade: Posting in #zm-collect-win.
- Why: channels.json says it is archived, but there is no archive event and people posted until Sep 30.

### SC-58 · ZM3D monster roster and boss rules (watch, caused 1 FAIL)
- Don't grade: The ZM3D monster roster or boss rules.
- Why: The Confluence GDD has the old roster (Punk, etc.) and a tray-fill boss end, but Robert's July Slack roster and the Docs boss GDD differ.

### SC-59 · Country of early adjoe installs (watch, caused 1 FAIL)
- Don't grade: The country of early adjoe installs (Apr 11-16).
- Why: Slack says they came almost all from Canada, but Snowflake codes every adjoe install US.

### SC-60 · Josh Dempsey identity mix-up (avoid, caused 1 FAIL)
- Don't grade: 'Josh Dempsey' as a person to DM/assign.
- Why: Named Josh Dempsey accounts are idle, but the work sits under EMPLOYEE_0032 (and Gmail's Josh is Solsten's sales director).
- Safer: Use EMPLOYEE_0032.

### SC-61 · ENG-2255 owner: Brian or Samuel (avoid, caused 1 FAIL)
- Don't grade: Who owns Linear ENG-2255.
- Why: Linear says Brian, but Brian says he assigned it to Samuel, and the unfurl shows Samuel.

### SC-62 · Reward import: hourly or every 5 minutes (avoid, caused 1 FAIL)
- Don't grade: The reward-import cadence.
- Why: The live-ops calendar says hourly in prod, but the CDK stack and Douglas's Sep 8 DM say every 5 minutes.

### SC-63 · Griffin pro-rata range math (avoid, caused 1 FAIL)
- Don't grade: A Griffin pro-rata dollar figure from the Aug 28 sync.
- Why: The Aug 28 sync says ~$545K-$1.2M, but that range fits a $2M raise, not the $2.5M ask in the same note.

### SC-64 · Node Media's two addresses (avoid, caused 1 FAIL)
- Don't grade: Node Media's address on its invoices.
- Why: Invoice 1158 says La Jolla, CA, but invoice 1168 says Miami, FL.

### SC-65 · ZM average playtime: 18 vs 37 min (avoid, caused 1 FAIL)
- Don't grade: ZM average playtime.
- Why: Leonard says ~18 min (30 with Variant C), but PLAYER_DAILY_PROGRESS means are 37-40 min (only the median is ~17).
- Safer: Define the measure and accept either.

### SC-66 · Closed analytics AWS account still active (avoid, caused 1 FAIL)
- Don't grade: Whether the analytics AWS account is live.
- Why: AWS mails say it was permanently closed (Aug 21), but Lambda notices keep arriving and Confluence/config still treat it as live.

### SC-67 · Build #1621 post lists #1620 (avoid, caused no FAILs yet)
- Don't grade: Which build contained PR #439, from the #builds post.
- Why: The #builds post is headed #1621, but its change list says #1620.

### SC-68 · Boss Arena on prod before its code (avoid, caused no FAILs yet)
- Don't grade: When Boss Arena reached prod.
- Why: Douglas's Aug 25 self-DM runs BossArena on prod, but the first BossArena commit is Sep 1.

### SC-69 · April 2025 BigQuery cost: 123.47 vs 126 (watch, caused no FAILs yet)
- Don't grade: The exact April 2025 BigQuery cost.
- Why: The receipt says $123.47, but the July P&L says 126.
- Safer: Use the receipt.

### SC-70 · Slack timezones all Los Angeles (avoid, caused no FAILs yet)
- Don't grade: A person's timezone from Slack.
- Why: Slack lists everyone as America/Los_Angeles, but calendars show real zones (Istanbul, El Salvador, UTC+3...).
- Safer: Use the calendar zone, non-decisively.

### SC-71 · ZOM-471 priority: high vs Medium (watch, caused no FAILs yet)
- Don't grade: ZOM-471's priority from the Linear-bot post.
- Why: The Linear-bot post says 'high priority', but Linear says Medium (never updated).

### SC-72 · Python 3.9 end-of-life date in the future (avoid, caused no FAILs yet)
- Don't grade: The Python 3.9 EOL as already passed.
- Why: AWS notices say EOL was 'reached on October 30, 2025', but that is after the Oct 1 clock.
- Safer: Use the Lambda deprecation dates.

### SC-73 · Monthly burn in pitch text (avoid, caused no FAILs yet)
- Don't grade: A monthly burn figure from pitch text.
- Why: Leonard's pitches say ~$100K/month, but he also says $55K and CASH_BALANCE says ~$58K.


## Task date Feb 14, 2026 (normal): 33 issues

### NB-1 · Slack channel access after Feb 11 (avoid, caused 37 FAILs)
- Don't grade: Who can read a Slack channel after 2026-02-11 20:40 UTC, or injected posts by someone in a channel they left.
- Why: Julia, Marcus, Owen, Oliver, Martin, Claire and Brian leave their channels on Feb 11 and Leonard makes them single-channel guests, but channels.json still lists them as members.
- Safer: Use #prototype, their DMs, or anything before Feb 11.

### NB-2 · P&L forecast sheet costs vs Snowflake (avoid, caused 9 FAILs)
- Don't grade: Monthly AWS, hosting, Unity licence, UA or salary costs from the "P&L and forecast - Jan 2026" sheet.
- Why: The sheet shows AWS = 0 and Jan salaries of $17,000, but Snowflake has AWS at $1,485–$2,745 every month, Jan salaries of $9,895.88 and all SaaS at $786.
- Safer: Use the vendor's own invoice or receipt.

### NB-3 · Cash at Dec 31 and Jan 31 (avoid, caused 8 FAILs)
- Don't grade: Cash on Dec 31, 2025 or Jan 31, 2026 from CASH_BALANCE, Leonard's email or the P&L sheet.
- Why: CASH_BALANCE says $25,031 for Dec 31, but Leonard's email and the P&L sheet say about $52,686. For Jan 31 Snowflake says $8,096 and the sheet $1,653.84.

### NB-4 · February 2026 payments: paid or only accrued (avoid, caused 7 FAILs)
- Don't grade: Whether a February 2026 payment was made (Sunset $11,700, legal $780, AWS and others), from FINANCE_LEDGER.
- Why: The ledger books them as cash out, but MONTHLY_BURN says February was only accrued and never closed, and Sunset's own email says its payment is still due.

### NB-5 · Helpshift effective date: February 29, 2025 (avoid, caused 7 FAILs)
- Don't grade: Helpshift's effective cancellation date from its acknowledgement email, or asks to "quote the vendor's exact date".
- Why: Helpshift's reply says "effective February 29,2025", a date that doesn't exist and is in the wrong year; Leonard had asked to stop at the end of the current billing cycle in Feb 2026.
- Safer: Grade only that Helpshift agreed to cancel.

### NB-6 · Slack messages after Feb 13 10:03 UTC (avoid, caused 7 FAILs)
- Don't grade: Slack messages from Feb 13 after 10:03 UTC.
- Why: The Slack export stops at 10:03 UTC on Feb 13, but two threads (BoomBit in C09SWTHEMEW, the #prototype "common rarity" thread) and three file uploads point to later messages that don't exist.

### NB-7 · Combo Fighter levels past 35 (avoid, caused 7 FAILs)
- Don't grade: How far Combo Fighter players got, or level funnels, from the warehouse.
- Why: The warehouse has hundreds of level 36–438 events from Dec 28 on, but the GDD and Robert (Feb 2) say players can't go past level 35.

### NB-8 · Helpshift payments left: $150×10 or ×8 (avoid, caused 6 FAILs)
- Don't grade: How many Helpshift payments are left, or "Leonard's latest count", from #winddown or #executives.
- Why: On Feb 9 in #winddown Leonard says "we owe Helpshift $150*10 = $1500", but on Feb 10 in #executives he writes "$150*8 months".
- Safer: Grade only that Helpshift agreed to cancel.

### NB-9 · Combo Fighter on Google Play (avoid, caused 5 FAILs)
- Don't grade: Whether Combo Fighter is on Google Play, or its Android players.
- Why: Robert says "we aren't official on google", but Snowflake has 206 Combo Fighter Android players and Play Store reviews.

### NB-10 · CASH_BALANCE burn column comment (avoid, caused 4 FAILs)
- Don't grade: The CASH_BALANCE.monthly_net_burn column comment (October 2025 payroll and headcount, "the UA row omits wires").
- Why: The comment says October 2025 payroll was $32,172 for 9 people and the UA row misses $46,201.50, but the data shows $39,292.02 for 13 people and the adjoe wires are already counted.

### NB-11 · Combo Fighter GDD is out of date (avoid, caused 4 FAILs)
- Don't grade: Combo Fighter ads, monetization or "parked" status taken from the Combo Fighter GDD in Confluence.
- Why: The GDD says the game "remains parked" and ads are "outside MVP scope", but Slack shows version 1.0.91 with in-app purchases (Feb 6) and interstitial ads turned on (Feb 12).

### NB-12 · Vendor names hidden behind tokens (avoid, caused 3 FAILs)
- Don't grade: A vendor or tool that a record names only as [PERSON_NAME_…] or [USERNAME_…], such as Bizxpress's subject line or the Helpshift row in the Task List.
- Why: Records like "cursor and [PERSON_NAME_20852]" replace the vendor's name with a token while other records spell it out, so nobody can tell they mean the same vendor.
- Safer: Use a record that spells out the name.

### NB-13 · Exact GitHub branch names (avoid, caused 3 FAILs)
- Don't grade: An exact GitHub branch name.
- Why: The branches list says marcus.bennett/…, but PRs and Slack say Marcus/….
- Safer: If you need one, accept both forms.

### NB-14 · ENG-2403/ENG-2404 checklists vs child tickets (avoid, caused 3 FAILs)
- Don't grade: The ENG-2403 and ENG-2404 checklists in Linear.
- Why: They tick items whose tickets are still in PR or Validation, and leave Done items unticked.
- Safer: Use the child tickets' own states.

### NB-15 · Employee or contractor (avoid, caused 3 FAILs)
- Don't grade: Whether someone was an employee or a contractor, from PAYROLL_ROSTER.
- Why: PAYROLL_ROSTER marks most people full_time, but Leonard says "no US employees, only international contractors" and the termination letters say "independent contractor".

### NB-16 · Domino Delights database: Aurora or DynamoDB (avoid, caused 3 FAILs)
- Don't grade: Which database Domino Delights runs on, from Confluence.
- Why: One Confluence page says Aurora PostgreSQL is the primary datastore; another says Domino Delights runs on DynamoDB.

### NB-17 · Domino Delights installs in Jan–Feb 2026 (avoid, caused 3 FAILs)
- Don't grade: Domino Delights paid, Brazil or worldwide installs in Jan–Feb 2026, from the warehouse.
- Why: BoomBit says the Brazil test went live, but Snowflake has only organic installs and zero Brazil installs ever.

### NB-18 · Domino Delights September 2025 installs (avoid, caused 2 FAILs)
- Don't grade: Domino Delights installs in September 2025, or adjoe credits for that month.
- Why: Slack says about 450 installs and adjoe turned on Sep 1, but Snowflake has zero Domino Delights installs that month.

### NB-19 · Unity Ads spend is $0 in the warehouse (avoid, caused 2 FAILs)
- Don't grade: Unity Ads dollar amounts from AD_SPEND_DAILY.
- Why: AD_SPEND_DAILY shows $0 on every Unity row, but Gmail has Unity Ads receipts and a $1,000 prepayment.

### NB-20 · Board marketing figure: $150K or $170K (avoid, caused 2 FAILs)
- Don't grade: What marketing figure the Board was told.
- Why: The board email body says about $150K, but the attached deck and the Slack drafts say $170K.

### NB-21 · Review app versions vs Slack builds (avoid, caused 2 FAILs)
- Don't grade: Joining review or warehouse app versions to Slack build numbers.
- Why: ZM3D reviews say 0.1.601/0.1.688 while Slack says 0.3.x, and Domino Delights reviews say 0.3.104 while Slack says 0.3.305.

### NB-22 · Contractor letter 00016874 termination date (avoid, caused 1 FAIL)
- Don't grade: A termination date from contractor letter 00016874.
- Why: It says January 31, 2025, but the sibling letters sent minutes later say January 31, 2026.

### NB-23 · Singular invoice INVSINC22671 has two totals (avoid, caused 1 FAIL)
- Don't grade: A Singular "as billed" total from invoice INVSINC22671.
- Why: The invoice exists twice with the same date: once for $25,000 and once for $6,250.

### NB-24 · Linear has no comments or links (avoid, caused 1 FAIL)
- Don't grade: What a base Linear comment said, or missing ticket links.
- Why: The Linear export has zero comments and zero links, but Slack and Gmail refer to comments on tickets.

### NB-25 · Domino Delights store history in 2024 roadmap (avoid, caused 1 FAIL)
- Don't grade: Domino Delights' store history from the 2024 roadmap page in Confluence.
- Why: The page says Domino Delights shipped as "Puzzles vs Zombies", but that was Zombie Match 3D.

### NB-26 · Combo Fighter sales: none or two (avoid, caused 1 FAIL)
- Don't grade: Whether Combo Fighter sold anything.
- Why: The QA runbook says no purchase was ever recorded, but Robert says "2 people bought rotation pack".

### NB-27 · Quest auto-deploy: on or off (avoid, caused 1 FAIL)
- Don't grade: Whether the quest auto-deploy is on, from the Quest System postmortem.
- Why: The postmortem body says the 8-hour auto-deploy "was left running", but its page comment says auto-deploy "stays off" until fixes ship.

### NB-28 · ZM3D Meta campaign end date (avoid, caused 1 FAIL)
- Don't grade: The end date of the ZM3D Meta campaign.
- Why: Leonard turned it off on Nov 3 at 03:13 UTC, but Snowflake still has $246.18 of spend dated Nov 3.

### NB-29 · Shutdown decision date: Feb 9 or 10 (watch, caused 1 FAIL)
- Don't grade: The shutdown decision date as the start of a time window.
- Why: On Feb 9 in #executives Leonard says "we finalized the decision to shut down", but on Feb 11 in #leapblock he says "We didn't finalize this decision until yesterday".

### NB-30 · Endless Monster Mode: live or removed (watch, caused 1 FAIL)
- Don't grade: Whether Endless Monster Mode (or Infinite Boss mode) is live, from the Live-Ops calendar.
- Why: The Live-Ops calendar says it is live, but Leonard said game modes are being removed (ZOM-642, still in Validation).

### NB-31 · Slack DM member lists (avoid, caused no FAILs yet)
- Don't grade: Who had access, argued from Slack DM or group-DM member lists.
- Why: Most 1:1 DMs list 3–5 members, and many group-DM names don't match their members.

### NB-32 · Douglas's end date: October or December (avoid, caused no FAILs yet)
- Don't grade: Douglas's end date.
- Why: The AWS Infrastructure Guide says he departed in Oct 2025, but the ledger pays him through Dec 31, 2025.

### NB-33 · December DAU drop from 70 to 24 (watch, caused no FAILs yet)
- Don't grade: Leonard's "DAU dropped from 70 to 24".
- Why: Leonard says DAU dropped from 70 to 24, but DAILY_ACTIVE_USERS shows 68 and 64 for Dec 26 and 27.
