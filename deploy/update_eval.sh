#!/usr/bin/env bash
# Install a new eval release built from the chosen zips. Runs as root. New runs pick it up
# immediately; runs already in progress keep the version they started with.
#   bash deploy/update_eval.sh                          # Evals.zip + claude.zip (default)
#   bash deploy/update_eval.sh claude Universe_Sonorra  # only these; everything else is reused
#   bash deploy/update_eval.sh --universes              # everything, including Universe_Sonorra.zip
# Universe_Sonorra.zip unpacks to Universe_Sonorra/ (single fixed-clock Sonorra v17 universe).
# Unchosen parts are reused from the current release: Evals/claude copied, universe hardlinked.
# The new release must pass the eval's own integrity check before the portal switches to it.
set -euo pipefail

ZIP_URL=${ZIP_URL:-$(grep -E '^EVAL_ZIP_URL=' /opt/eval-portal/.env 2>/dev/null | cut -d= -f2- | awk '{print $1}' || true)}
ZIP_URL=${ZIP_URL:-https://static.remotasks.com/uploads/6ae1792488162884415d5ee6}
ROOT=/srv/eval-releases
LINK=/srv/eval-base
UNIVERSES="Universe_Sonorra"
ALL="Evals claude $UNIVERSES"
dest() {
  echo "$1"
}

if [ "${1:-}" = "--universes" ]; then CHOSEN="$ALL"; elif [ $# -gt 0 ]; then CHOSEN="$*"; else CHOSEN="Evals claude"; fi
for name in $CHOSEN; do case " $ALL " in *" $name "*) ;; *) echo "unknown file: $name" >&2; exit 2;; esac; done
# Evals pins the hashes of the original claude/skills files; a reused claude/ already has the portal's effort
# rewrite, so the pins wouldn't match. Install the two together (claude.zip is tiny) so skill_effort re-pins.
case " $CHOSEN " in *" Evals "*|*" claude "*) CHOSEN="$CHOSEN Evals claude";; esac
chosen() { case " $CHOSEN " in *" $1 "*) return 0;; *) return 1;; esac; }

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NEW="$ROOT/$STAMP"
ZIPS="$ROOT/zips"
mkdir -p "$NEW" "$ZIPS"
CURRENT=$(readlink -f "$LINK" 2>/dev/null || true)
trap 'echo "update failed; still using ${CURRENT:-the previous release}"; [ "$(readlink -f "$LINK" 2>/dev/null)" = "$NEW" ] || rm -rf "$NEW"' ERR
echo "STEP building release $STAMP from: $CHOSEN"

fetch() {  # resumable download; keeps the response headers for SOURCE.json
  rm -f "$ZIPS/$1.zip"
  if [ -f "$ZIPS/manual/$1.zip" ]; then
    # Copied in by hand (the CDN in front of the zip links can serve a stale copy for hours after an upload):
    # use it once, recording its own md5 and size as the ETag and length.
    echo "STEP using the hand-copied $1.zip"
    mv "$ZIPS/manual/$1.zip" "$ZIPS/$1.zip"
    printf 'etag: "%s"\ncontent-length: %s\nlast-modified: %s\n' "$(md5sum "$ZIPS/$1.zip" | cut -d' ' -f1)" \
      "$(stat -c %s "$ZIPS/$1.zip")" "$(date -u -R)" > "$ZIPS/$1.zip.head"
  else
    echo "STEP downloading $1.zip"
    curl -sfIL "$ZIP_URL/$1.zip" | tr -d '\r' > "$ZIPS/$1.zip.head" || true
    curl -fL --retry 5 --retry-delay 5 -sS -C - -o "$ZIPS/$1.zip.part" "$ZIP_URL/$1.zip"
    mv "$ZIPS/$1.zip.part" "$ZIPS/$1.zip"
  fi
  echo "STEP unpacking $1.zip"
  local parent="$NEW"
  mkdir -p "$parent"
  unzip -q -n "$ZIPS/$1.zip" -d "$parent"
  rm -rf "$parent/__MACOSX"
}

for name in Evals claude; do
  if chosen "$name" || [ -z "$CURRENT" ] || [ ! -d "$CURRENT/$name" ]; then
    fetch "$name"
  else
    cp -a "$CURRENT/$name" "$NEW/$name"   # a real copy: the skill-effort step below edits these files
  fi
done
for name in $UNIVERSES; do
  if chosen "$name" || [ -z "$CURRENT" ] || [ ! -d "$CURRENT/$(dest "$name")" ]; then
    fetch "$name"  # the GitHub parts: all three are chosen together (above), so they unpack into one folder
    # Universe zips are 1-6 GB each and later releases hardlink the unzipped copy, so free the disk.
    [ "${KEEP_ZIPS:-0}" = 1 ] || rm -f "$ZIPS/$name.zip"
  elif [ ! -d "$NEW/$(dest "$name")" ]; then  # the second and third GitHub part: the folder is already linked
    mkdir -p "$NEW/$(dirname "$(dest "$name")")"
    cp -al "$CURRENT/$(dest "$name")" "$NEW/$(dest "$name")"  # hardlink: no copy, no extra disk (read-only in use)
  fi
done

# Strip macOS zip metadata, prepare mount points, register the skills.
rm -rf "$NEW/__MACOSX"
find "$NEW" -name .DS_Store -delete 2>/dev/null || true
for d in $ALL; do [ -d "$NEW/$(dest "$d")" ] || { echo "missing $(dest "$d") after unzip" >&2; exit 1; }; done
rm -rf "$NEW/Evals/task_inputs" "$NEW/Evals/.cache"
mkdir -p "$NEW/Evals/task_inputs" "$NEW/Evals/.cache"
rm -rf "$NEW/.claude"
python3 "$NEW/claude/install.py"
# Run the skills at the portal's effort (EVAL_EFFORT in .env, default high) instead of their pinned xhigh.
EFFORT=$(grep -E '^EVAL_EFFORT=' /opt/eval-portal/.env 2>/dev/null | cut -d= -f2 | awk '{print $1}' || true)
(cd /opt/eval-portal && python3 -m server.skill_effort "$NEW/claude/skills" "${EFFORT:-high}")
# Portal-owned fixes to eval tools (each only for the exact file version it was written for).
(cd /opt/eval-portal && python3 -m server.bundle_patches "$NEW")

# SOURCE.json: for each zip, the metadata (ETag, Last-Modified, size) of the copy this release holds.
python3 - "$NEW" "$ZIPS" "$CURRENT" $ALL <<'PY'
import json, os, sys, time
new, zips, current, names = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
old = {}
if current and os.path.isfile(os.path.join(current, "SOURCE.json")):
    old = json.load(open(os.path.join(current, "SOURCE.json"))).get("files", {})
files = {}
for name in names:
    head = os.path.join(zips, name + ".zip.head")
    if os.path.isfile(head):
        meta = {}
        for line in open(head):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        size = meta.get("content-length", "")
        files[name] = {"etag": meta.get("etag", "").strip('"'), "last_modified": meta.get("last-modified"),
                       "bytes": int(size) if size.isdigit() else None,
                       "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        os.remove(head)
    elif name in old:
        files[name] = old[name]  # reused from the previous release
json.dump({"release": os.path.basename(new), "files": files}, open(os.path.join(new, "SOURCE.json"), "w"), indent=1)
PY

# Refuse to switch to a release the eval itself would reject.
echo "STEP checking the new release"
python3 -I - "$NEW" <<'PY'
import sys
root = sys.argv[1]
sys.path.insert(0, root + "/Evals/tools")
import hg_eval_release
for stage in ("injection", "prompt", "rubric", "stumping", "all-fail-validation", "reference-notes"):
    hg_eval_release.snapshot(stage, root)
print("release check OK for all 6 stages")
PY
chown -R root:root "$NEW"; chmod -R a+rX,go-w "$NEW"

echo "STEP switching to the new release"
ln -sfn "$NEW" "$LINK.tmp" && mv -T "$LINK.tmp" "$LINK"
echo "eval base now -> $NEW"
# Keep the two newest releases (older ones may still back a running container until it ends).
ls -1d "$ROOT"/2* | sort | head -n -2 | while read -r old; do
  [ "$old" = "$NEW" ] || rm -rf "$old"
done
