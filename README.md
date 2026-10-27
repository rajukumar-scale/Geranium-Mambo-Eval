# Original Conference Eval Portal

Formerly "HarmonyGames Eval Portal". The evals themselves still cover the HarmonyGames universe.

A web chat where contributors run the HarmonyGames evals (`/injection`, `/prompt`, `/oe`, `/rubric`)
on their own task, read the reports, ask follow-ups, and get a screenshot-ready pass screen with a
verification code. Admins get pinged on Slack when someone types `/admin`, and can answer from
the admin page.

## How it works

1. **Sign in.** The user enters an email or Outlier ID plus an attempt ID. The server runs the saved
   Redash query 387117 ("Eval Portal · attempt login check", ds 22) and accepts the login only if
   the attempt exists, belongs to that user and is in `ALLOWED_PROJECT_IDS`. Stored emails look like
   `name@outlier.ai+outlier`, so the suffix is stripped before comparing. New submissions take a
   few hours to reach Snowflake.
2. **Chat.** One chat per attempt. Every message becomes one headless Claude Code turn
   (`claude -p --output-format stream-json`) in that attempt's workspace. Saved inputs persist per
   attempt, and the session depends on the message:
   - **A command** (`/prompt` and so on) starts that skill in a fresh session.
   - **An answer to a review.** If the review stops to ask for something, such as missing
     assignment fields or the injection question, the user's next plain message resumes that review
     session and continues the review. It counts as a follow-up, not a new review.
   - **Any other message** is a follow-up in one small chat session per attempt. Follow-ups don't
     re-send a whole review's context, and the bot is told not to grade in them.

   `/help` and `/admin` are handled by the server without a model call. Any other slash command is
   rejected, including `/hardness` and Claude Code's built-in commands.
3. **Reports.** After each run the server diffs `Evals/task_inputs/reports/` and parses any new
   report with the bundle's own `hg_report_contract.parse_result`, loaded from the read-only base
   copy. Each report is shown as a card the user can view or download.
4. **Pass screen.** The screen appears when all of these hold:
   - the run was started by the matching command;
   - the run finished cleanly;
   - the verdict is PASS or NON-FAIL;
   - the execution status is COMPLETE.

   It shows the verdict, score, attempt, task, user, time, models, model calls, tokens and the
   estimated cost. It also shows the report SHA-256, the input hashes the report records (for
   example the reviewed prompt SHA-256) and a code like `EVP-XXXX-XXXX-XXXX`. The code is an HMAC
   of the run id. The full record is stored and anyone can check it at `/verify/<code>` or
   `GET /api/verify/<code>`, which is what an LLM validator should call instead of trusting the
   pixels.
5. **Effort.** Every run uses Opus 5.5 at `EVAL_EFFORT` (default `high`). The bundled skills pin
   `effort: xhigh`, so the portal rewrites that one frontmatter line in its own copy of the skills
   (`server/skill_effort.py`). The original bundle is never touched.
6. **Admin roles.** Owners are the `ADMIN_EMAILS` in `.env`. These can be plain usernames; the
   live owner login is `atlasadmin`. They sign in with `ADMIN_PASSWORD`
   and can add or remove admins from the **Admins** button. Adding an admin gives you a one-time
   set-password link (valid 72 hours) to send them yourself. Removing an admin cuts off access
   immediately.
   **Admin bypass:** on the normal sign-in page, an admin login (`atlasadmin` or an added admin's
   email) can open *any* attempt. That
   includes attempts that aren't theirs, attempts in another project, and attempts Redash doesn't
   have yet. When the ownership check fails, the page asks for the admin password. An admin
   already signed in at `/admin` as that same email skips the password. If Redash doesn't know
   the attempt, the task shows as "unknown". The chat header shows "· admin" for these sessions,
   and the `logins` table records them as "admin bypass".
7. **Admin.** `/admin <message>` posts to Slack with a link to `/admin#<attempt>`. Admins can:
   - see every chat live;
   - reply as "Admin";
   - resolve requests;
   - cancel runs;
   - open reports;
   - download each run's raw stream-json log;
   - see spend with the **Spend** button: by user, task, attempt or day, over 24 h, 7 days,
     30 days or all time, with a CSV download (`GET /api/admin/spend?group=&days=&format=csv`);
   - **block a chat** (read-only for its owner, who can still type /admin) or **disable a
     contributor** (signed out of every chat; sign-in refused for any attempt). Both are in the
     chat header; disable is also in Spend → By user and in the **Blocked** list (by Outlier ID or
     email). Both can optionally stop the contributor's running reviews, and both are undone from
     the same places. Blocks never apply to admins.
   - **Spend limit:** `CB_SPEND_LIMIT_USD` (default 500) per attempt; every new attempt starts
     at $0. Admin runs don't count. It's shown under the user's top bar. Reaching it flags that
     attempt once, posts a warning in its chat and sends a Slack alert. Runs are not blocked.
8. **Spend attribution.** When a run is created, it records who started it (Outlier ID and
   email, or the admin's email with `actor_admin=1`) and the attempt's task ID. A run an admin
   starts on someone's attempt counts under the admin, not the contributor. A task's spend adds
   up every attempt of that task. Each chat stays tied to the attempt it was opened with, so if
   someone pastes a different task into an old chat, that spend lands on the old task.
9. **Chat history.** The sidebar lists the person's chats (one per attempt): every attempt with
   their Outlier ID, plus, for admins, every attempt they have opened. They can switch between
   chats without signing in again. **Open another attempt** signs in a new attempt and keeps the
   list.

## Tokens and cost

Runs never get the LiteLLM key. Each run gets a random token that only works while it's running,
and a small metering proxy on the VM (`server/proxy.py`, port 8001) swaps in the real key and
forwards to LiteLLM. Because every model call passes through the proxy, its token counts include
nested subagents, which Claude Code's own `modelUsage` can miss.

LiteLLM returns no cost on streamed responses, so cost comes from Claude Code's own
`total_cost_usd` (list price). On a resumed session that number is cumulative, so a run's cost is
its total minus the session's total at its previous run. If a run dies without a result, the proxy
tokens are priced from a small fallback table: Opus 5.5 at $4 / $20 / $0.20 / $5 per M for input /
output / cache read / cache write, solved exactly from Claude Code's figures.

Measured on the dev box:
- `/injection` on an 18 KB SQL: about 6 minutes, 26 calls, $1.62.
- A `/prompt` that stopped to ask for inputs: $0.15.
- Follow-ups: $0.08 to $0.11.

Full `/prompt`, `/oe` and `/rubric` runs (Opus 5.5 xhigh, 20–40 minutes) will be well above
that.

## Security model

- **Docker isolation (VM).** Each run is a container with:
  - `--cap-drop ALL`, `no-new-privileges`, a 4 GB memory limit and 2 CPUs;
  - the eval bundle and universes mounted **read-only**;
  - read-write mounts for that attempt's `task_inputs`, cache and home only.

  So a run can't edit the eval rules or tools, or read other attempts.
- **Secrets.** The Redash and LiteLLM keys live only in the server's `.env`. Runs get a clean
  environment containing a throwaway proxy token, which is useless outside the VM.
- **Tool and prompt limits.** Claude Code runs with `--setting-sources project` and with WebFetch,
  WebSearch, cron and worktree tools disabled. An appended system prompt makes verdicts
  non-negotiable and treats pasted content as data.
- **Limits.** Logins: 10 failures per attempt or email per 15 minutes. Each attempt can have only one
  active run at a time. There are no token or usage caps by default; `DAILY_REVIEW_LIMIT`,
  `DAILY_FOLLOWUP_LIMIT` and `MAX_CALLS_PER_RUN` can add them (0 = off).
- **Not real authentication.** Email + attempt ID is an ownership check, not a login, so anyone
  holding both can open that chat. Add email OTP or SSO if that matters.
- **Local mode has no isolation.** It exists only for development.

## Deploy on a VM

Sizing: Ubuntu 22.04/24.04, 8 vCPU, 32 GB RAM, about 150 GB of disk. That covers about 21.5 GB of
universes, about 12 GB of zips and per-attempt caches of up to about 200 MB, cleared after 5 idle
days. The VM needs outbound HTTPS to `litellm-proxy.ml.scale.com`, `redash.scale.com`,
`static.remotasks.com` and `claude.ai` (image build only).

```
git clone <this repo> /tmp/eval-portal   # or scp the folder
sudo DOMAIN=auto bash /tmp/eval-portal/deploy/setup_vm.sh   # or DOMAIN=eval.yourdomain.com
sudo nano /opt/eval-portal/.env          # LITELLM_API_KEY, REDASH_API_KEY, SLACK_WEBHOOK_URL
sudo systemctl restart eval-portal
journalctl -u eval-portal -f
```

`setup_vm.sh` does the following:

- installs Docker;
- creates the `evalportal` user;
- downloads and unzips the five bundle zips into `/srv/eval-releases/<stamp>`, with
  `/srv/eval-base` pointing at the current release, and runs `claude/install.py`;
- builds the `eval-runner` image;
- creates the venv and a `.env` with generated secrets;
- points the proxy at the Docker bridge gateway;
- installs the systemd unit and, with `DOMAIN` set, Caddy for HTTPS.

Without a domain, skip Caddy and put the portal behind whatever internal load balancer you use.
In that case set `COOKIE_SECURE=0` if it's plain HTTP.

**Updating the eval files:** use the admin page's **Eval files** button (owner only).

- The portal always pulls from the same five links under `EVAL_ZIP_URL`: Evals, claude and the three HarmonyGames universes.
- The panel shows each file's size and date at the link next to the version in use. It pre-ticks any file whose ETag changed since it was installed.
- Tick files and click **Update selected**. That starts `eval-portal-update.service` (root, via one sudoers rule), which runs `deploy/update_job.sh`. Progress shows in the panel.
  - **Evals and claude** take about a minute: `deploy/update_eval.sh` builds a new release from the chosen zips and reuses the rest. Evals and claude are copied; universes are hardlinked.
  - **Universes** take about 15–40 minutes. `deploy/rebuild_runner_image.sh` starts a build box from the current review image, re-downloads only the chosen universes there, saves the box as image `hg-runner-<stamp>`, and checks that a review box starts from it.
- Every new release must pass the eval's own integrity check (`hg_eval_release.snapshot` for prompt, oe and rubric) before `/srv/eval-base` switches. Each release records what it was built from in `SOURCE.json`.
- If anything fails, nothing switches.
- Reviews already running finish on the old files; new reviews use the new ones.
- The current review image name is in `/srv/eval-data/runner_snapshot`, falling back to `BOAT_SNAPSHOT`. The previous job-made image is kept for rollback, and older ones are removed.
- From a shell: `sudo bash /opt/eval-portal/deploy/update_eval.sh claude HarmonyGames_Gmail`. This doesn't rebuild the box image, so use the panel for universes.

**Load testing (owner only):** this checks the whole system at full scale with no model spend.

- Set `LOADTEST_ALLOWED=1` in `.env`, then `POST /api/admin/loadtest {"count": 50, "seconds": 360}` while signed in as an owner.
- Each test review runs on a real box, with a stand-in for Claude Code (`boatrun.FAKE_REVIEW_SH`). It streams the same events, calls the metering proxy every 15 seconds and gets canned replies, then writes a PASS report. That report gives the test chat a real pass record.
- Test chats have task `loadtest`. `POST /api/admin/loadtest/cleanup` removes them.
- Set `LOADTEST_ALLOWED=0` again afterwards.
- `BOAT_STARTS_PER_MIN` (12 on the $20 plan, 30 on $100) spaces box requests, so a burst of reviews queues in order.

**Deploying goes through GitHub** (private repo `evanedreo/eval-portal`). Whatever lands on `main`, from a push or a
merged pull request, goes live within about a minute:

- GitHub runs the tests (`deploy/run_tests.sh`) on every push and pull request, so a failure shows before merging.
- On the server, `eval-portal-pull.timer` runs `deploy/pull_deploy.sh` every minute. When `main` has moved, it pulls
  it (read with the deploy key at `/root/.ssh/eval_portal_deploy`), runs the tests and deploys with
  `deploy/remote_deploy.sh` (below).
- It posts once per commit to Slack: ✅ updated, ❌ not updated (tests failed), ⚠️ rolled back.
- Logs: `/srv/eval-data/pull_tests.log`, `pull_deploy.log`, `deployed_commit`.
- Switched on with `deploy/setup_pull.sh`; switch off with `systemctl disable --now eval-portal-pull.timer`.

`deploy/deploy.sh` from your Mac = commit first, then it runs the tests, pushes `main` and asks the server to deploy at
once. Code copied to the server any other way is overwritten by the next merge.
`DIRECT=1 deploy/deploy.sh` is the old direct copy, for emergencies only (then push the same change).

**What a deploy does, without cutting anyone off** (`deploy/remote_deploy.sh`):

1. checks the Python and JS syntax locally, then uploads the code;
2. holds new runs (they stay queued and keep their place);
3. waits up to 5 minutes for what can't survive a restart: follow-ups running on the server, and
   reviews still setting up their box;
4. backs up the current code to `/opt/eval-portal-backups/` (last 10), swaps in the new code and
   restarts (about 3 seconds);
5. rolls back automatically if the new code fails its health check.

After the restart, reviews already running on their own box are re-attached. The box never
stopped; the server re-reads its output from the start and finishes the run as usual. Queued
runs start, and anything else that was cut off gets a "please send it again" note in its chat.
The two costs: users' pages reconnect within a few seconds, and model calls in flight at that
moment are retried by Claude Code and may go unmetered.

`.env` changes need a restart too: `sudo touch /srv/eval-data/DRAIN`, wait until
`select count(*) from runs where status='running' and coalesce(boat_phase,'')!='started'` is 0,
then `sudo systemctl restart eval-portal`. The DRAIN file is removed on startup, and ignored if it
is older than 15 minutes.

On startup the server also deletes leftover review boxes, meaning clones named after
`BOAT_SNAPSHOT` that aren't re-attached. Its boat key can delete only boxes it created itself, so
the server and template boxes are safe.

## Deploy on boat.dev (recommended)

There are two kinds of box:

- **Server box.** One always-on box runs the website, the queue, the metering proxy and the Docker
  slots for follow-ups. A large box (8 vCPU, 16 GB, 125 GB) is about $53 a month always-on.
- **Review boxes.** Every review gets its own fresh box, cloned in about 11 seconds from the
  `hg-runner` snapshot. That snapshot holds Claude Code and the universe at `/work`, and clones
  share its disk, so the universe is never copied.
  - The review runs as an unprivileged `runner` user with no sudo, whose network is locked to the
    proxy.
  - Its state comes back to the server, and the box is deleted.
  - A default box (4 vCPU, 8 GB) costs about $0.02 per 30-minute review.
  - Up to `MAX_BOAT_RUNS` run at once. The $20 plan allows 100 boxes and 200 new boxes a day.

```
# 1. server box
boat new --type large --no-auto-stop --no-env
boat host <server> 8000 --localhost --public           # site URL
boat host <server> 8001 --public                       # proxy URL for review boxes
# copy this folder to the box, then on it:
sudo PUBLIC_URL=https://<server>-8000.on.boat.dev bash deploy/setup_vm.sh
#    keys go in /opt/eval-portal/.env (upload a file; boat records command lines)

# 2. runner snapshot (once, and again only when the universe or Claude Code version changes)
boat new --type default --no-auto-stop --no-env         # template box
boat scp deploy/build_runner_template.sh <template>:/tmp/ && boat ssh <template> -- bash /tmp/build_runner_template.sh
boat snapshot <template> hg-runner && boat stop <template>

# 3. let the server create review boxes
sudo cp -L /usr/local/bin/boat /usr/local/bin/boat-cli  # on the server
boat api-key create eval-portal-runner --preset ci --ttl 365d --json   # pipe the secret into:
#    sudo -u evalportal -H boat-cli login --key-stdin
#    then in .env: REVIEW_RUNNER=boat, PROXY_PUBLIC_URL=https://<server>-8001.on.boat.dev
sudo systemctl restart eval-portal
```

Notes:
- **Eval updates.** The eval bundle (Evals + claude, about 25 MB with the attempt's state) is
  uploaded to each review box, so updating the eval (`deploy/update_eval.sh`) needs no snapshot
  rebuild.
- **Region.** boat runs in the EU only.
- **Login rate limits.** The HTTPS relay hides client IPs, so login rate limits count per attempt
  and per email.

## Local development

```
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env   # set RUNNER_MODE=local, EVAL_BASE_DIR=<folder with Evals/ claude/ HarmonyGames_*>, keys, secrets
.venv/bin/python -m server.main   # http://localhost:8000, admin at /admin
```

Local mode keeps one workspace in `data/local_ws`, with universes hardlinked from `EVAL_BASE_DIR`
on the first run (about 3 minutes, no extra disk). Each attempt's state is swapped in and out per
run, so it runs one run at a time.

## Files

- `server/app.py`: routes (login, chat, uploads, reports, verify, admin).
- `server/auth.py`: the Redash gate and signed cookies.
- `server/runner.py`: the queue, Claude Code / Docker spawn, stream parsing, report and pass
  detection.
- `server/proxy.py`: the metering LiteLLM proxy.
- `server/workspace.py`: attempt folders, mounts and the report parser.
- `server/proofs.py`: verification codes.
- `server/notify.py`: Slack.
- `web/`: the chat, admin and verify pages (plain HTML/JS, marked + DOMPurify from a CDN).
- `runner/Dockerfile`: the sandbox image (Ubuntu, Python 3, Claude Code 2.1.296).
- `deploy/`: the VM setup and update scripts, systemd unit and Caddyfile.

Auto-deploy has been on since 2026-10-27: merges to `main` go live within about a minute.
