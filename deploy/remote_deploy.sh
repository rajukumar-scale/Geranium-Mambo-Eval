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

# Hold new runs (they stay queued and survive the restart), then wait for what can't be re-attached:
# follow-ups running in Docker here, and reviews still setting up their box.
touch "$DATA/DRAIN"; chown "$OWNER" "$DATA/DRAIN"
trap 'rm -f "$DATA/DRAIN"' EXIT
# (Before the first deploy of re-attach support there is no boat_phase column: then every running run counts.)
if q "select boat_phase from runs limit 0" 2>/dev/null; then
  BUSY_SQL="select count(*) from runs where status='running' and coalesce(boat_phase,'')!='started'"
  LIVE_SQL="select count(*) from runs where status='running' and boat_phase='started'"
else
  BUSY_SQL="select count(*) from runs where status='running'"
  LIVE_SQL="select 0"
fi
for i in $(seq 1 300); do
  BUSY=$(q "$BUSY_SQL")
  [ "$BUSY" = 0 ] && break
  [ $((i % 15)) = 1 ] && echo "waiting for $BUSY run(s) to finish or start before restarting…"
  sleep 1
done
if [ "$BUSY" != 0 ]; then echo "still $BUSY busy after 5 min; deploy aborted, nothing changed"; exit 1; fi

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
for unit in eval-portal.service eval-portal-update.service eval-portal-pull.service eval-portal-pull.timer; do
  [ -e "$APP/deploy/$unit" ] || continue
  if ! cmp -s "$APP/deploy/$unit" "/etc/systemd/system/$unit"; then
    install -m 644 "$APP/deploy/$unit" "/etc/systemd/system/$unit"; RELOAD=1; echo "$unit updated"
  fi
done
[ "$RELOAD" = 1 ] && systemctl daemon-reload
# The one root command the portal may run: start the update job (checked with visudo before installing).
if ! cmp -s "$APP/deploy/eval-portal-update.sudoers" /etc/sudoers.d/eval-portal-update; then
  visudo -cf "$APP/deploy/eval-portal-update.sudoers" >/dev/null && \
    install -m 440 "$APP/deploy/eval-portal-update.sudoers" /etc/sudoers.d/eval-portal-update && echo "sudoers rule updated"
fi
chmod 755 "$APP/deploy/"*.sh
systemctl restart eval-portal
for i in $(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:8000/healthz && break; sleep 1; done
if ! curl -sf -o /dev/null http://127.0.0.1:8000/healthz; then
  echo "new code failed its health check; rolling back to $STAMP"
  for item in server web deploy README.md; do rm -rf "$APP/$item"; cp -a "$BACKUP/$item" "$APP/$item"; done
  systemctl restart eval-portal
  exit 1
fi
echo "deployed $STAMP: $LIVE running review(s) re-attached, $QUEUED queued run(s) resumed (backup in $BACKUP)"
