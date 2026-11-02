# Portal prompts (plan-your-task kickoff, fix helper)

Portal-owned text for the "plan your task" kickoff (server/plan.py, pilot `PLAN_PILOT`). Not part of the eval release
and not copied into review workspaces: the portal reads these files itself. Changing one is a normal commit.

- plan_kickoff_card.md: the chat card posted once per attempt, before the contributor's first /prompt (Markdown, shown
  to the contributor). It suggests they ask "Check my task idea against the hardness and tool-call guides." and ends
  with links (/?guide=hardness, /?guide=toolcalls) that open those Guide tabs in the chat page.
- early_guard.md: one bullet added to the chat assistant's standing instructions (server/runner.py GUARD) for every
  chat, as the last bullet of "Helping them build their task". It covers the request the card suggests. Changing it
  changes GUARD_VERSION, so every chat's next follow-up starts a fresh session on the new rules.

Each file is used whole, after any leading `<!-- ... -->` comments, which are dropped (keep maintainer notes there).
The early guard points the assistant at guidelines/known_data_issues.md, which deploy/build_known_issues.py writes next
to server/known_issues.json.
Final text (2026-10-31) is the same as ~/Downloads/plan_feature_2026-10-31/content/ (kickoff_card.md ->
plan_kickoff_card.md, early_guard.md -> early_guard.md); NOTES.md there explains the choices. A "Plan my task with the
guides" button with a hidden planning note was dropped the same day (one planning run wasn't reliable enough). The
portal logs a warning at startup if PLAN_PILOT is on and either file says PLACEHOLDER, and a test fails on it.

fix_helper_rules.md (server/fix_helper.py, pilot `FIX_HELPER_PILOT`, 2026-11-01): the rules sent, hidden, with the
contributor's answer to the fix-help offer that follows their 2nd and later /prompt FAILs. Same text as
~/Downloads/plan_feature_2026-10-31/content/fix_helper_rules_v5.md, except its first line: the answer is quoted at the
end of the note instead of living in intent.md. It goes on that one message only, not into GUARD, so editing it doesn't
start fresh sessions. The offer texts are in server/fix_helper.py.
