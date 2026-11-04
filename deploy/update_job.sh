#!/usr/bin/env bash
# The admin page's "Update selected" job: systemd unit eval-portal-update.service, as root.
# Reads the chosen files from /srv/eval-data/update/request.json. If a universe is chosen, the review box
# image is rebuilt first; then the new release is installed (and checked); then reviews switch to the new
# image. Progress goes to update.log, state to status.json (both read by the admin page).
set -euo pipefail
D=/srv/eval-data/update
APP=/opt/eval-portal
REQ=$D/request.json STATUS=$D/status.json LOG=$D/update.log
mkdir -p "$D"

st() {  # st key=value ...: merge into status.json
  python3 - "$STATUS" "$@" <<'PY'
import json, os, sys, time
path = sys.argv[1]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
for kv in sys.argv[2:]:
    key, value = kv.split("=", 1)
    data[key] = value
data["updated_at"] = time.time()
json.dump(data, open(path + ".tmp", "w"))
os.replace(path + ".tmp", path)
os.chmod(path, 0o644)
PY
}

FILES=$(python3 -c 'import json, sys; print(" ".join(json.load(open(sys.argv[1]))["files"]))' "$REQ")
: > "$LOG"; chmod 644 "$LOG"
exec >> "$LOG" 2>&1
st state=running files="$FILES" started_at="$(date +%s)" finished_at= error= release= snapshot=
trap 'st state=failed finished_at="$(date +%s)" error="$(grep -v "^STEP" "$LOG" | tail -2 | tr "\n" " " | cut -c1-300)"' ERR
echo "STEP update started for: $FILES"

UNIS=""
for f in $FILES; do case "$f" in *HarmonyGames_*) UNIS="$UNIS $f";; esac; done
NEWSNAP=""
RUNNER=$(grep -E '^REVIEW_RUNNER=' "$APP/.env" 2>/dev/null | cut -d= -f2 | awk '{print $1}' || true)
if [ -n "$UNIS" ] && [ "$RUNNER" = modal ]; then
  echo "STEP reviews run on Modal: new universes reach them at their next start"
elif [ -n "$UNIS" ]; then
  bash "$APP/deploy/rebuild_runner_image.sh" $UNIS | tee "$D/rebuild.out"
  NEWSNAP=$(tail -1 "$D/rebuild.out")
fi

bash "$APP/deploy/update_eval.sh" $FILES

if [ -n "$NEWSNAP" ]; then
  echo "$NEWSNAP" > /srv/eval-data/runner_snapshot.tmp
  chown evalportal:evalportal /srv/eval-data/runner_snapshot.tmp
  mv /srv/eval-data/runner_snapshot.tmp /srv/eval-data/runner_snapshot
  echo "STEP new reviews now start from $NEWSNAP"
  # Keep this image and the previous job-made one (rollback); remove older ones. The original
  # hg-runner image is never removed.
  sudo -u evalportal -H /usr/local/bin/boat-cli snapshots --no-update --json | python3 -c '
import json, sys
data = json.load(sys.stdin)
items = data if isinstance(data, list) else next((v for v in data.values() if isinstance(v, list)), [])
names = sorted(s["name"] for s in items if (s.get("name") or "").startswith("hg-runner-2"))
print("\n".join(names[:-2]))' | while read -r old; do
    if [ -n "$old" ]; then  # an if, not &&: an empty line must not fail the job under set -e
      sudo -u evalportal -H /usr/local/bin/boat-cli snapshot rm --no-update "$old" >/dev/null && echo "removed old image $old" || echo "could not remove $old"
    fi
  done
fi

st state=done finished_at="$(date +%s)" release="$(basename "$(readlink -f /srv/eval-base)")" \
   snapshot="$(cat /srv/eval-data/runner_snapshot 2>/dev/null || echo hg-runner)"
echo "STEP done"
