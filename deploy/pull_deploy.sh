#!/bin/bash
# Auto-deploy (runs as root every minute, from eval-portal-pull.timer): when main on GitHub has a new commit, run the
# tests, then deploy it with remote_deploy.sh (running reviews keep going; a failed health check rolls back).
# Reports to Slack: ✅ updated, ❌ not updated (tests failed / deploy error), ⚠️ rolled back. One message per commit:
# a broken commit is reported once, then skipped until main moves on. Set up once with deploy/setup_pull.sh.
set -euo pipefail
APP=${EVP_APP:-/opt/eval-portal}
SRC=${EVP_SRC:-/opt/eval-portal-src}
KEY=${EVP_KEY:-/root/.ssh/eval_portal_deploy}
DATA=$(grep -E '^PORTAL_DATA_DIR=' $APP/.env 2>/dev/null | cut -d= -f2 | awk '{print $1}' || true)
DATA=${DATA:-/srv/eval-data}
DEPLOY=${EVP_DEPLOY:-remote_deploy.sh}  # the tests point this at a stand-in

exec 9>"${EVP_LOCK:-/run/eval-portal-pull.lock}"
if command -v flock > /dev/null; then flock -n 9 || exit 0; fi  # a deploy is already running (no flock: a Mac test run)
[ -d $SRC/.git ] || exit 0  # not set up yet

[ -f "$KEY" ] && export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git -C $SRC fetch -q origin main || exit 0  # GitHub unreachable: try again next minute
NEW=$(git -C $SRC rev-parse origin/main)
OLD=$(cat $DATA/deployed_commit 2>/dev/null || true)
[ "$NEW" = "$OLD" ] && exit 0
[ "$NEW" = "$(cat $DATA/failed_commit 2>/dev/null || true)" ] && exit 0  # already reported
# After a deploy gave up because the portal was busy, wait before trying again: each try holds new chat replies for up
# to 5 minutes, and retrying every minute meant CBs waited again and again (2026-11-01).
[ "$(date +%s)" -lt "$(cat $DATA/deploy_backoff 2>/dev/null || echo 0)" ] && exit 0

git -C $SRC checkout -q --force --detach "$NEW"
WEB=$(git -C $SRC remote get-url origin | sed -E 's#^git@github.com:#https://github.com/#; s#\.git$##')
WHAT="${NEW:0:7} \"$(git -C $SRC log -1 --format=%s "$NEW")\" ($(git -C $SRC log -1 --format=%an "$NEW"))"
if [ -n "$OLD" ] && git -C $SRC cat-file -e "$OLD" 2>/dev/null; then
  COUNT=$(git -C $SRC rev-list --count "$OLD..$NEW")
  [ "$COUNT" -gt 1 ] && WHAT="$COUNT changes, latest $WHAT"
fi
LIVE=${OLD:0:7}; LIVE=${LIVE:-the previous version}
notify() { (cd $APP && PORTAL_ENV_FILE=$APP/.env $APP/.venv/bin/python -m server.notify_cli "$1") || true; }
fail() { echo "$NEW" > $DATA/failed_commit; notify "$1"; exit 1; }
echo "$(date -u +%FT%TZ) deploying $WHAT"

if ! PYTHON=$APP/.venv/bin/python bash $SRC/deploy/run_tests.sh > $DATA/pull_tests.log 2>&1; then
  fail "❌ Portal NOT updated: tests failed on $WHAT. Live is still on $LIVE. Details: $WEB/commit/$NEW"
fi

STAGE=$(mktemp -d /tmp/evp-pull.XXXXXX)
trap 'rm -rf "${STAGE:?}"' EXIT
git -C $SRC archive "$NEW" | tar x -C "$STAGE"
set +e
bash "$STAGE/deploy/$DEPLOY" "$STAGE" > $DATA/pull_deploy.log 2>&1
rc=$?
set -e
cat $DATA/pull_deploy.log
if [ $rc = 0 ]; then
  echo "$NEW" > $DATA/deployed_commit
  rm -f $DATA/failed_commit $DATA/deploy_backoff
  REVIEWS=$(grep -oE '[0-9]+ running review\(s\) re-attached' $DATA/pull_deploy.log | grep -oE '^[0-9]+' || echo 0)
  notify "✅ Portal updated: $WHAT. $REVIEWS running review(s) kept going."
elif grep -q "deploy aborted, nothing changed" $DATA/pull_deploy.log; then
  echo $(( $(date +%s) + ${EVP_BACKOFF_SEC:-1800} )) > $DATA/deploy_backoff
  exit 0  # still busy on this server; nothing changed, try again in 30 minutes
elif grep -q "rolling back" $DATA/pull_deploy.log; then
  fail "⚠️ Portal update rolled back: $WHAT failed its health check after restart, so the previous version ($LIVE) was put back. Live is fine; the change needs a fix."
else
  fail "❌ Portal NOT updated: the deploy of $WHAT failed ($(tail -n 1 $DATA/pull_deploy.log)). Live is still on $LIVE."
fi
