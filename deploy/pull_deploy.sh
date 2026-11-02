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

# GitHub Deployments, environment "production": the merge commit and each merged PR's head commit show whether the
# change is live (the PR page shows "deployed to production"). Needs GITHUB_DEPLOY_TOKEN in .env (fine-grained, this
# repo only, Deployments: read and write); without it this is skipped. Best effort: GitHub never blocks a deploy.
GH_TOKEN=$(grep -E '^GITHUB_DEPLOY_TOKEN=' $APP/.env 2>/dev/null | cut -d= -f2- || true)
GH_REPO=${WEB#https://github.com/}
GH_URL=$(grep -E '^PUBLIC_BASE_URL=' $APP/.env 2>/dev/null | cut -d= -f2- || true)
GH_SHAS="$NEW"
if [ -n "$OLD" ] && git -C $SRC cat-file -e "$OLD" 2>/dev/null; then  # the PR heads merged since the live version
  for m in $(git -C $SRC rev-list --merges "$OLD..$NEW"); do GH_SHAS="$GH_SHAS $(git -C $SRC rev-parse "$m^2")"; done
fi
gh_api() {  # method path json
  ${EVP_CURL:-curl} -sS -m 20 -X "$1" -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$GH_REPO/$2" -d "$3" 2>/dev/null || true
}
gh_deployment() {  # sha -> deployment id, made once per sha
  local f=$DATA/github_deployment_$1 id
  id=$(cat "$f" 2>/dev/null || true)
  if [ -z "$id" ]; then
    id=$(gh_api POST deployments "{\"ref\":\"$1\",\"environment\":\"production\",\"auto_merge\":false,\"required_contexts\":[],\"description\":\"Eval portal\"}" \
         | grep -oE '"id": *[0-9]+' | head -1 | grep -oE '[0-9]+' || true)
    [ -n "$id" ] && echo "$id" > "$f"
  fi
  echo "$id"
}
github() {  # state description (no quotes): queued, in_progress, success, failure, error, inactive
  [ -n "$GH_TOKEN" ] || return 0
  local sha id
  for sha in $GH_SHAS; do
    id=$(gh_deployment "$sha")
    [ -n "$id" ] && gh_api POST "deployments/$id/statuses" \
      "{\"state\":\"$1\",\"description\":\"$2\",\"environment_url\":\"$GH_URL\",\"auto_inactive\":false}" > /dev/null
  done
  return 0
}
github_done() {  # after a successful deploy: earlier deployments that never went live were replaced by this one
  [ -n "$GH_TOKEN" ] || return 0
  local f sha
  for f in $DATA/github_deployment_*; do
    [ -e "$f" ] || continue
    sha=${f##*_}
    case " $GH_SHAS " in *" $sha "*) ;; *) gh_api POST "deployments/$(cat "$f")/statuses" \
      "{\"state\":\"inactive\",\"description\":\"Replaced by a newer deploy\"}" > /dev/null;; esac
    rm -f "$f"
  done
}
fail() { echo "$NEW" > $DATA/failed_commit; notify "$1"; github "${2:-failure}" "${3:-Not deployed}"; exit 1; }
# At most one deploy an hour (2026-11-02): each restart holds new chat replies while it waits for the running ones, and
# with several merges an hour CBs waited again and again. Merges in the hour go out together. touch $DATA/deploy_now
# to skip the wait once (e.g. an urgent fix).
EVERY=${EVP_DEPLOY_EVERY_SEC:-3600}
LAST_AT=$(cat $DATA/last_deploy_at 2>/dev/null || echo 0)
if [ ! -e $DATA/deploy_now ] && [ $(( $(date +%s) - LAST_AT )) -lt "$EVERY" ]; then
  if [ "$(cat $DATA/deploy_gate_noted 2>/dev/null || true)" != "$NEW" ]; then  # say it once per commit, not every minute
    echo "$NEW" > $DATA/deploy_gate_noted
    github queued "Goes out with the next hourly deploy, at $(date -u -d @$((LAST_AT + EVERY)) +%H:%M 2>/dev/null || date -u -r $((LAST_AT + EVERY)) +%H:%M) UTC"
  fi
  exit 0
fi
rm -f $DATA/deploy_now
echo "$(date -u +%FT%TZ) deploying $WHAT"
github in_progress "Testing, then deploying once the server is free"

if ! PYTHON=$APP/.venv/bin/python bash $SRC/deploy/run_tests.sh > $DATA/pull_tests.log 2>&1; then
  fail "❌ Portal NOT updated: tests failed on $WHAT. Live is still on $LIVE. Details: $WEB/commit/$NEW" failure "Tests failed, not deployed"
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
  date +%s > $DATA/last_deploy_at
  rm -f $DATA/failed_commit $DATA/deploy_backoff
  REVIEWS=$(grep -oE '[0-9]+ running review\(s\) re-attached' $DATA/pull_deploy.log | grep -oE '^[0-9]+' || echo 0)
  notify "✅ Portal updated: $WHAT. $REVIEWS running review(s) kept going."
  github success "Live"
  github_done
elif grep -q "deploy aborted, nothing changed" $DATA/pull_deploy.log; then
  echo $(( $(date +%s) + ${EVP_BACKOFF_SEC:-1800} )) > $DATA/deploy_backoff
  github queued "Server busy with reviews, trying again at $(date -u -d @$(cat $DATA/deploy_backoff) +%H:%M 2>/dev/null || date -u -r $(cat $DATA/deploy_backoff) +%H:%M) UTC"
  exit 0  # still busy on this server; nothing changed, try again in 30 minutes
elif grep -q "rolling back" $DATA/pull_deploy.log; then
  fail "⚠️ Portal update rolled back: $WHAT failed its health check after restart, so the previous version ($LIVE) was put back. Live is fine; the change needs a fix." failure "Failed its health check, rolled back"
else
  fail "❌ Portal NOT updated: the deploy of $WHAT failed ($(tail -n 1 $DATA/pull_deploy.log)). Live is still on $LIVE." error "Deploy failed, not deployed"
fi
