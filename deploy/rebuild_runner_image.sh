#!/usr/bin/env bash
# Rebuild the review box image with a fresh copy of Universe_Sonorra (runs as root on the server).
# Boat commands run as `evalportal`, whose key owns the boxes and images it creates.
#   bash deploy/rebuild_runner_image.sh Universe_Sonorra
#   bash deploy/rebuild_runner_image.sh --tools-only    # no universe re-download: refresh the tools layer only
# Starts a build box from the current image, re-downloads Universe_Sonorra there, saves the box as
# a new image (gm-runner-<stamp>), checks a review box starts from it, and prints the image name last.
set -euo pipefail
[ $# -gt 0 ] || { echo "usage: $0 <universe>... | --tools-only" >&2; exit 2; }
[ "$1" = --tools-only ] && shift
DATA=/srv/eval-data
KEEP=$DATA/update/keep_boxes        # boxes the startup sweep must not delete while this runs
B="sudo -u evalportal -H /usr/local/bin/boat-cli"
ACTIVE=$(cat "$DATA/runner_snapshot" 2>/dev/null || true)
ACTIVE=${ACTIVE:-$(grep -E '^BOAT_SNAPSHOT=' /opt/eval-portal/.env 2>/dev/null | cut -d= -f2 | awk '{print $1}' || true)}
ACTIVE=${ACTIVE:-gm-runner}
NEWSNAP="gm-runner-$(date -u +%Y%m%d-%H%M%S)"  # boat image names: lowercase letters, digits, dashes
touch "$KEEP"

new_box() {  # prints the id of a ready box cloned from image $1
  $B new --no-update --from "$1" --type default --no-env --ttl 14400 --json | python3 -c '
import json, sys
box = None
for line in sys.stdin:
    try:
        event = json.loads(line)
    except ValueError:
        continue
    box = event.get("id") or box
    if event.get("event") == "error":
        sys.exit("boat new failed: %s" % event.get("error"))
    if event.get("event") == "ready" and box:
        print(box)
        sys.exit(0)
sys.exit("boat new did not report a ready box")'
}
BOXES=""
cleanup() {
  for b in $BOXES; do $B delete --no-update "$b" --yes >/dev/null 2>&1 || true; sed -i "/^$b\$/d" "$KEEP"; done
}
trap cleanup EXIT

echo "STEP starting a build box from $ACTIVE"
BOX=$(new_box "$ACTIVE"); BOXES="$BOX"; echo "$BOX" >> "$KEEP"
echo "build box $BOX"
$B scp --no-update /opt/eval-portal/deploy/build_runner_template.sh "$BOX:/tmp/build_runner_template.sh" >/dev/null
$B scp --no-update /opt/eval-portal/deploy/setup_similarity.sh "$BOX:/tmp/setup_similarity.sh" >/dev/null
box_path() {
  echo "/work/$1"
}
REMOVE=""
for n in "$@"; do REMOVE="$REMOVE $(box_path "$n")"; done
echo "STEP downloading ${*:-no universes (tools only)} on the build box"
# Run in the background on the box and poll, so a dropped SSH session can't kill a 20-minute download.
$B ssh --no-update "$BOX" -- "sudo rm -rf $REMOVE && { nohup bash /tmp/build_runner_template.sh > /tmp/build.log 2>&1 < /dev/null & echo \$! > /tmp/build.pid; }" >/dev/null
for i in $(seq 1 720); do
  sleep 10
  OUT=$($B ssh --no-update "$BOX" -- 'tail -c 400 /tmp/build.log; kill -0 "$(cat /tmp/build.pid)" 2>/dev/null && echo __RUNNING__ || true' 2>/dev/null || true)
  case "$OUT" in *RUNNER_TEMPLATE_READY*) break;; esac
  case "$OUT" in *__RUNNING__*) [ $((i % 30)) = 0 ] && echo "  still downloading/unpacking ($((i / 6)) min)";; *)
    if [ -n "$OUT" ]; then echo "build failed on the box:"; echo "$OUT" | tail -5; exit 1; fi;; esac
done
case "$OUT" in *RUNNER_TEMPLATE_READY*) ;; *) echo "build did not finish in 2 hours"; exit 1;; esac
$B ssh --no-update "$BOX" -- 'du -sh /work/* 2>/dev/null' | sed 's/^/  /'

echo "STEP saving the new box image $NEWSNAP"
$B snapshot --no-update "$BOX" "$NEWSNAP" | tail -1

echo "STEP checking a review box starts from $NEWSNAP"
TEST=$(new_box "$NEWSNAP"); BOXES="$BOXES $TEST"; echo "$TEST" >> "$KEEP"
CHECK="id runner >/dev/null && /usr/local/bin/claude --version >/dev/null && test -f /opt/simcheck/model/model.onnx && /opt/simcheck/bin/python -c 'import onnxruntime, tokenizers, numpy' && test -d /work/Universe_Sonorra"
$B ssh --no-update "$TEST" -- "$CHECK && echo IMAGE_OK" | grep -q IMAGE_OK || { echo "the new image is missing parts"; exit 1; }
echo "image check OK"
echo "$NEWSNAP"
