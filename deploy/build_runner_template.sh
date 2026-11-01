#!/usr/bin/env bash
# Builds the per-review runner image on a fresh boat box (run as the default `user`, with sudo):
# Claude Code + Python tools + the universes at /work (root-owned, read-only: the two dated universes
# /work/Universe_Normal/Normal_HarmonyGames_X and /work/Universe_SynthCut/SynthCut_HarmonyGames_X, each from the zip
# of the same name; Normal's GitHub from three parts) and an
# unprivileged `runner` user without sudo that every review runs as, plus the /opt/simcheck similarity runtime
# (copy deploy/setup_similarity.sh to /tmp first). Then snapshot the box:
#   boat snapshot <box> hg-runner
set -euo pipefail
ZIP_URL=${ZIP_URL:-https://static.remotasks.com/uploads/6a5595596cf3031de47f0ea2}
CLAUDE_VERSION=${CLAUDE_VERSION:-2.1.296}

sudo apt-get update -q
sudo apt-get install -y -q python3 python3-docx poppler-utils ripgrep unzip curl iptables

curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION"
sudo cp -L "$HOME/.local/bin/claude" /usr/local/bin/claude
sudo chmod 755 /usr/local/bin/claude
/usr/local/bin/claude --version

id runner >/dev/null 2>&1 || sudo useradd --create-home --shell /bin/bash runner
sudo deluser runner sudo >/dev/null 2>&1 || true

sudo mkdir -p /work
sudo rm -rf /work/HarmonyGames_GitHub /work/HarmonyGames_Gmail /work/HarmonyGames_Other_Services  # pre-2026-10-20 layout
for u in Normal SynthCut; do
  for name in HarmonyGames_GitHub HarmonyGames_Gmail HarmonyGames_Other_Services; do
    target="/work/Universe_$u/${u}_$name"
    [ -d "$target" ] && continue
    zips="${u}_$name"
    [ "$u/$name" = "Normal/HarmonyGames_GitHub" ] && zips="${u}_${name}_part1of3 ${u}_${name}_part2of3 ${u}_${name}_part3of3"
    sudo mkdir -p "/work/Universe_$u"
    for z in $zips; do
      curl -fL --retry 5 --retry-delay 5 -C - -s -o "/tmp/$z.zip" "$ZIP_URL/$z.zip"
      sudo unzip -q -n "/tmp/$z.zip" -d "/work/Universe_$u"  # the parts are disjoint: -n merges them
      sudo rm -rf "/work/Universe_$u/__MACOSX"
      rm -f "/tmp/$z.zip"
    done
    # an older-style zip holds the folder without the date prefix: rename it into place
    if [ ! -d "$target" ] && [ -d "/work/Universe_$u/$name" ]; then sudo mv "/work/Universe_$u/$name" "$target"; fi
    [ -d "$target" ] || { echo "missing $target after unzip" >&2; exit 1; }
  done
  sudo rm -rf "/work/Universe_$u/__MACOSX"
done
sudo rm -rf /work/__MACOSX
sudo find /work -name .DS_Store -delete
sudo chown -R root:root /work
sudo chmod -R a+rX,go-w /work
du -sh /work/*
# Dataset similarity runtime for /prompt (offline on review boxes); rebuild_runner_image.sh copies the script here.
if [ ! -f /opt/simcheck/model/model.onnx ]; then bash /tmp/setup_similarity.sh; fi
echo RUNNER_TEMPLATE_READY
