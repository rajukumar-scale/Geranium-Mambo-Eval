#!/bin/bash
# Server half of deploy.sh (runs as root). Usage: remote_deploy.sh <unpacked new code dir>
set -euo pipefail
NEW=$1
APP=/opt/eval-portal
DATA=$(grep -E '^PORTAL_DATA_DIR=' $APP/.env 2>/dev/null | cut -d= -f2 | awk '{print $1}' || true)
DATA=${DATA:-/srv/eval-data}
DB=$DATA/portal.db
OWNER=$(stat -c %U:%G $APP/server/app.py)
q() { sqlite3 "$DB" "$1"; }

$APP/.venv/bin/python -m compileall -q "$NEW/server" >/dev/null

# Reviews running on this server (boat_phase='server', the server-first commands) can't be re-attached and take
# 5-15 minutes. First stop new ones from starting here (DEPLOY_PENDING: they go to boat instead, so nobody waits) and
# let the running ones finish, up to 20 minutes, before holding anything back.
touch "$DATA/DEPLOY_PENDING"; chown "$OWNER" "$DATA/DEPLOY_PENDING"
trap 'rm -f "$DATA/DEPLOY_PENDING"' EXIT
# Runs in a detached container (runs.container set, 2026-11-02) are re-attached after the restart, so only older ones
# count. (Before that column exists, every one counts.)
# Container runs (chat replies, server reviews) survive the restart and are re-attached, so the deploy needn't wait for
# them, but only if the LIVE code leaves them running when it stops (_container_running, 2026-11-02: before that its
# shutdown removed them, which cut 4 runs at 08:38).
DETACHED=""
if grep -q "_container_running" $APP/server/runner.py 2>/dev/null && q "select container from runs limit 0" 2>/dev/null; then
  DETACHED=" and container is null"
fi
if q "select boat_phase from runs limit 0" 2>/dev/null; then
  for i in $(seq 1 1200); do
    ON_SERVER=$(q "select count(*) from runs where status='running' and boat_phase='server'$DETACHED")
    [ "$ON_SERVER" = 0 ] && break
    [ $((i % 60)) = 1 ] && echo "waiting for $ON_SERVER review(s) running on this server to finish…"
    sleep 1
  done
fi

# Hold new follow-ups (they stay queued and survive the restart), then wait for what can't be re-attached: follow-ups
# and reviews running in Docker here. Reviews on boat keep starting: one still setting up its box when the portal
# restarts is simply started again (Runner.start), so waiting for those made busy-hour deploys give up again and again
# while every new run was held (2026-11-01: 80 minutes, 3 tries).
touch "$DATA/DRAIN"; chown "$OWNER" "$DATA/DRAIN"
trap 'rm -f "$DATA/DRAIN" "$DATA/DEPLOY_PENDING"' EXIT
# (Before the first deploy of re-attach support there is no boat_phase column: then every running run counts.)
if q "select boat_phase from runs limit 0" 2>/dev/null; then
  BUSY_SQL="select count(*) from runs where status='running' and (kind='followup' or boat_phase='server')$DETACHED"
  LIVE_SQL="select count(*) from runs where status='running' and boat_phase='started'"
else
  BUSY_SQL="select count(*) from runs where status='running'"
  LIVE_SQL="select 0"
fi
# Up to 12 minutes (5 until 2026-11-02): chat replies run up to ~10 minutes, and with 3-4 at once there was nearly
# always one still going after 5, so every busy-hour deploy gave up (5 changes waited 3.5 hours). New ones are held
# meanwhile, so the count only goes down. Keep it under runner.draining()'s 15-minute stale limit.
for i in $(seq 1 720); do
  BUSY=$(q "$BUSY_SQL")
  [ "$BUSY" = 0 ] && break
  [ $((i % 15)) = 1 ] && echo "waiting for $BUSY run(s) running on this server to finish before restarting…"
  sleep 1
done
if [ "$BUSY" != 0 ]; then echo "still $BUSY busy after 12 min; deploy aborted, nothing changed"; exit 1; fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/opt/eval-portal-backups/$STAMP
mkdir -p "$BACKUP"
cp -a $APP/server $APP/web $APP/deploy $APP/README.md "$BACKUP"/ 2>/dev/null || true
ls -1dt /opt/eval-portal-backups/* | tail -n +11 | xargs -r rm -rf   # keep the last 10

for item in server web deploy README.md .env.example; do
  rm -rf "$APP/$item.new"; cp -a "$NEW/$item" "$APP/$item.new"; chown -R "$OWNER" "$APP/$item.new"
  rm -rf "$APP/$item.old"; [ -e "$APP/$item" ] && mv "$APP/$item" "$APP/$item.old"
  mv "$APP/$item.new" "$APP/$item"; rm -rf "$APP/$item.old"
done

LIVE=$(q "$LIVE_SQL")
QUEUED=$(q "select count(*) from runs where status='queued'")
RELOAD=0
for unit in eval-portal.service eval-portal-meter.service eval-portal-update.service eval-portal-pull.service eval-portal-pull.timer \
            eval-similarity-refresh.service eval-similarity-refresh.timer; do
  [ -e "$APP/deploy/$unit" ] || continue
  if ! cmp -s "$APP/deploy/$unit" "/etc/systemd/system/$unit"; then
    install -m 644 "$APP/deploy/$unit" "/etc/systemd/system/$unit"; RELOAD=1; echo "$unit updated"
  fi
done
[ "$RELOAD" = 1 ] && systemctl daemon-reload
# Dataset similarity (/prompt STEP 0.6): the offline runtime, and the hourly library refresh into the live release.
if [ -e "$APP/deploy/setup_similarity.sh" ]; then
  [ -f /opt/simcheck/model/model.onnx ] || bash "$APP/deploy/setup_similarity.sh" || echo "similarity runtime install failed (reviews report the check as SKIPPED)"
  systemctl is-enabled -q eval-similarity-refresh.timer 2>/dev/null || systemctl enable -q --now eval-similarity-refresh.timer || true
fi
# The one root command the portal may run: start the update job (checked with visudo before installing).
if ! cmp -s "$APP/deploy/eval-portal-update.sudoers" /etc/sudoers.d/eval-portal-update; then
  visudo -cf "$APP/deploy/eval-portal-update.sudoers" >/dev/null && \
    install -m 440 "$APP/deploy/eval-portal-update.sudoers" /etc/sudoers.d/eval-portal-update && echo "sudoers rule updated"
fi
chmod 755 "$APP/deploy/"*.sh
# The metering proxy (eval-portal-meter) carries running reviews' model calls, so it is left running: only the portal
# restarts. The meter restarts only when its own code (server/proxy.py) changed, and then waits for in-flight calls.
PROXY_PORT=$(grep -E '^PROXY_PORT=' $APP/.env 2>/dev/null | cut -d= -f2 | awk '{print $1}' || true)
METER_URL=http://127.0.0.1:${PROXY_PORT:-8001}/__meter_health
METER=none
if [ -e "$APP/deploy/eval-portal-meter.service" ]; then
  if ! systemctl is-active -q eval-portal-meter; then METER=first
  elif ! cmp -s "$BACKUP/server/proxy.py" "$APP/server/proxy.py"; then METER=restart; fi
fi
if [ "$METER" = first ]; then
  # Switch-over: the old portal process still serves the proxy port, so stop it before the meter takes the port.
  echo "starting the metering proxy as its own service"
  systemctl stop eval-portal
  systemctl enable -q eval-portal-meter
  systemctl start eval-portal-meter
  systemctl start eval-portal
else
  if [ "$METER" = restart ]; then
    echo "server/proxy.py changed: restarting the metering proxy (it waits for in-flight model calls)"
    systemctl restart eval-portal-meter
  fi
  systemctl restart eval-portal
fi
healthy() {
  curl -sf -o /dev/null http://127.0.0.1:8000/healthz || return 1
  [ "$METER" = none ] || curl -sf -o /dev/null "$METER_URL"
}
for i in $(seq 1 30); do healthy && break; sleep 1; done
if ! healthy; then
  echo "new code failed its health check; rolling back to $STAMP"
  for item in server web deploy README.md; do rm -rf "$APP/$item"; cp -a "$BACKUP/$item" "$APP/$item"; done
  for unit in eval-portal.service eval-portal-meter.service; do
    if [ -e "$BACKUP/deploy/$unit" ]; then install -m 644 "$BACKUP/deploy/$unit" "/etc/systemd/system/$unit"; fi
  done
  if [ ! -e "$BACKUP/deploy/eval-portal-meter.service" ]; then  # back to the proxy inside the portal process
    systemctl disable -q --now eval-portal-meter 2>/dev/null || true
    rm -f /etc/systemd/system/eval-portal-meter.service
  fi
  systemctl daemon-reload
  [ "$METER" = restart ] && systemctl restart eval-portal-meter
  systemctl restart eval-portal
  exit 1
fi
echo "deployed $STAMP: $LIVE running review(s) re-attached, $QUEUED queued run(s) resumed (backup in $BACKUP)"
