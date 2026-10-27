#!/bin/bash
# Deploys go through GitHub: the server deploys main by itself within a minute of a push or merge (deploy/pull_deploy.sh).
# Run from your Mac:
#   deploy/deploy.sh                    commit first; this runs the tests, pushes main and waits for the server to deploy it
#   DIRECT=1 deploy/deploy.sh [box]     emergency only (GitHub down): copy this folder straight to the server. The next
#                                       merge to main overwrites it, so commit and push the same change afterwards.
# Reviews on their own boxes keep running and the restarted server re-attaches to them. Runs that haven't started stay
# queued. A deploy waits (up to 5 min) for follow-ups and box setups in flight.
set -euo pipefail
BOX=${1:-bx_qnsgbtzk}
BOAT=${BOAT:-$HOME/.ascii/bin/boat}
cd "$(dirname "$0")/.."

if [ "${DIRECT:-}" != 1 ]; then
  if [ -n "$(git status --porcelain -- server web deploy tests README.md .env.example)" ]; then
    echo "Not deployed: there are uncommitted changes. Commit them (git add … && git commit), then run this again:"
    git status --short -- server web deploy tests README.md .env.example
    exit 1
  fi
  [ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "Not deployed: switch to the main branch first."; exit 1; }
  bash deploy/run_tests.sh
  git push -q origin main
  echo "Pushed $(git log -1 --format='%h "%s"'). Asking the server to deploy it now…"
  "$BOAT" ssh "$BOX" -- 'sudo systemctl start eval-portal-pull.service; sudo cat /srv/eval-data/deployed_commit; sudo tail -n 1 /srv/eval-data/pull_deploy.log 2>/dev/null' \
    | { read -r live; read -r last || true
        if [ "$live" = "$(git rev-parse HEAD)" ]; then echo "Live: ${live:0:7}. ${last:-}"
        else echo "Live is still ${live:0:7}; the server reported: ${last:-nothing yet}. It retries every minute (Slack says why)."; exit 1; fi; }
  exit 0
fi

python3 -m py_compile server/*.py
if command -v node >/dev/null; then for f in web/*.js; do node --check "$f"; done; fi

TGZ=$(mktemp -t evp-code).tgz
trap 'rm -f "$TGZ"' EXIT
COPYFILE_DISABLE=1 tar czf "$TGZ" --exclude __pycache__ --exclude .DS_Store server web deploy README.md .env.example
"$BOAT" scp "$TGZ" "$BOX:/tmp/evp-code.tgz" >/dev/null
"$BOAT" ssh "$BOX" -- 'sudo bash -c "mkdir -p /tmp/evp-new && tar xzf /tmp/evp-code.tgz -C /tmp/evp-new 2>/dev/null; bash /tmp/evp-new/deploy/remote_deploy.sh /tmp/evp-new; rc=\$?; rm -rf /tmp/evp-new /tmp/evp-code.tgz; exit \$rc"'
