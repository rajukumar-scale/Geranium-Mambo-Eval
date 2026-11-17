# Eval portal: rules for working on the live server

## The live database (`/srv/eval-data/portal.db` on bx_7r4qxme3)

The portal and the meter service (`eval-portal-meter`, ~5 model-call inserts a second while reviews run) both write
to it all the time. A slow or half-finished ad-hoc write holds the lock, other writes time out, and runs crash on
"database is locked" (2026-11-02: four chats were stuck until fixed by hand).

- Query it with the helper, never with a one-off `/tmp/*.py` or a raw `sqlite3` write:
  `cd /opt/eval-portal && sudo -u evalportal .venv/bin/python -m server.dbq "select ..."`
- Reads are read-only by default. Changes need `--write`; they run as ONE short transaction (`--file x.sql` for
  several statements). Always guard updates (`... where id=? and status='running'`).
- No bulk deletes/updates/VACUUM of the big tables (`messages`, `llm_calls`, `runs`) while reviews are running.
- Check `select count(*) from runs where status in ('queued','running')` before anything heavy.

## Stuck runs

A chat is "busy" while it has a `queued`/`running` run (`db.active_run`). The runner closes crashed and orphaned
runs itself (`Runner._fail_crashed`, `_orphan_sweeper`, every 60 s for runs older than 2 min). If one still sticks,
find it with `journalctl -u eval-portal | grep 'crashed'` and close it with a guarded `dbq --write`.

## Deploying

Merge to `main`; `eval-portal-pull.timer` tests and deploys it (restarts the portal only, running reviews re-attach).
It aborts if a server-side review is still running after 5 min and backs off 30 min. The meter is NOT restarted by
deploys: changes to `server/db.py`/`server/proxy.py` reach it only after `sudo systemctl restart eval-portal-meter`.
