#!/bin/bash
# Ship this checkout to the boat server without cutting off running reviews. Run it from your Mac:
#   deploy/deploy.sh                 # default server box
#   deploy/deploy.sh bx_xxxxxxxx     # another box
# Reviews on their own boxes keep running and the restarted server re-attaches to them. Runs that
# haven't started stay queued. The deploy waits (up to 5 min) for follow-ups and box setups in flight.
set -euo pipefail
BOX=${1:-bx_qnsgbtzk}
BOAT=${BOAT:-$HOME/.ascii/bin/boat}
cd "$(dirname "$0")/.."

python3 -m py_compile server/*.py
if command -v node >/dev/null; then for f in web/*.js; do node --check "$f"; done; fi

TGZ=$(mktemp -t evp-code).tgz
trap 'rm -f "$TGZ"' EXIT
COPYFILE_DISABLE=1 tar czf "$TGZ" --exclude __pycache__ --exclude .DS_Store server web deploy README.md .env.example
"$BOAT" scp "$TGZ" "$BOX:/tmp/evp-code.tgz" >/dev/null
"$BOAT" ssh "$BOX" -- 'sudo bash -c "mkdir -p /tmp/evp-new && tar xzf /tmp/evp-code.tgz -C /tmp/evp-new 2>/dev/null; bash /tmp/evp-new/deploy/remote_deploy.sh /tmp/evp-new; rc=\$?; rm -rf /tmp/evp-new /tmp/evp-code.tgz; exit \$rc"'
