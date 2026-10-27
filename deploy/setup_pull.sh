#!/bin/bash
# One-time switch-on of auto-deploy on the server (run as root). Needs the deploy key at /root/.ssh/eval_portal_deploy,
# added to the GitHub repo. Clones the repo, checks that main matches what's live (so the first auto-deploy doesn't
# surprise anyone), records it as deployed, and starts the every-minute timer. Stop it again with:
#   systemctl disable --now eval-portal-pull.timer
set -euo pipefail
REPO=${1:-git@github.com:evanedreo/eval-portal.git}
APP=/opt/eval-portal
SRC=/opt/eval-portal-src
DATA=$(grep -E '^PORTAL_DATA_DIR=' $APP/.env 2>/dev/null | cut -d= -f2 | awk '{print $1}' || true)
DATA=${DATA:-/srv/eval-data}
export GIT_SSH_COMMAND="ssh -i /root/.ssh/eval_portal_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

[ -d $SRC/.git ] || git clone -q "$REPO" $SRC
git -C $SRC fetch -q origin main
HEAD=$(git -C $SRC rev-parse origin/main)
git -C $SRC checkout -q --force --detach "$HEAD"

# main must match the live code, or the first auto-deploy would change things nobody meant to deploy
DIFF=$(cd $SRC && for f in $(git ls-files server web deploy README.md .env.example); do
  cmp -s "$f" "$APP/$f" || echo "  $f"; done)
if [ -n "$DIFF" ] && [ "${FORCE:-}" != 1 ]; then
  echo "main ($HEAD) differs from the live code in:"; echo "$DIFF"
  echo "Deploy or commit so they match first (or run with FORCE=1). Auto-deploy NOT switched on."
  exit 1
fi
echo "$HEAD" > $DATA/deployed_commit
rm -f $DATA/failed_commit
for unit in eval-portal-pull.service eval-portal-pull.timer; do
  install -m 644 "$APP/deploy/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now eval-portal-pull.timer
echo "auto-deploy on: live = main ${HEAD:0:7}; checks GitHub every minute"
