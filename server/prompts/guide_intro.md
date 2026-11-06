<!-- The guide pop-up (server/plan.py, guide_intro). Shown before the first /prompt until the contributor has opened all
three guides and pressed "I've read all three". Markdown; the three guide buttons are added below it by the page. -->
**Please read these 3 guides before you start. This is really important.**

Most tasks that fail review fail for reasons these guides cover: the task is too easy for Opus, it has too few tool calls, or its traps all fail the same way.

Reading them first saves you rounds of /prompt and /hardness later.

**One rule for your env:** everything you add goes in your SQL. Don't add or edit data by hand in the env explorer. Before your final runs, redeploy the env and apply only your final SQL: an old draft you ran earlier leaves its rows behind, and QC fails the task for data your SQL doesn't create.

Open each one below. You can come back to them any time with the **Guide** button at the top right.
