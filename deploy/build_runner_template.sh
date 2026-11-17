#!/usr/bin/env bash
# Builds the per-review runner image on a fresh boat box (run as the default `user`, with sudo):
# Claude Code + Python tools + Universe_Sonorra at /work (root-owned, read-only) and an
# unprivileged `runner` user without sudo that every review runs as, plus the /opt/simcheck similarity runtime
# (copy deploy/setup_similarity.sh to /tmp first). Then snapshot the box:
#   boat snapshot <box> gm-runner
set -euo pipefail
ZIP_URL=${ZIP_URL:-https://static.remotasks.com/uploads/6ae1792488162884415d5ee6}
CLAUDE_VERSION=${CLAUDE_VERSION:-2.1.296}

sudo apt-get update -q
sudo apt-get install -y -q python3 python3-docx python3-openpyxl poppler-utils ripgrep unzip curl iptables

curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION"
sudo cp -L "$HOME/.local/bin/claude" /usr/local/bin/claude
sudo chmod 755 /usr/local/bin/claude
/usr/local/bin/claude --version

id runner >/dev/null 2>&1 || sudo useradd --create-home --shell /bin/bash runner
sudo deluser runner sudo >/dev/null 2>&1 || true

sudo mkdir -p /work
target="/work/Universe_Sonorra"
if [ ! -d "$target" ]; then
  curl -fL --retry 5 --retry-delay 5 -C - -s -o "/tmp/Universe_Sonorra.zip" "$ZIP_URL/Universe_Sonorra.zip"
  sudo unzip -q -n "/tmp/Universe_Sonorra.zip" -d "/work"
  rm -f "/tmp/Universe_Sonorra.zip"
  [ -d "$target" ] || { echo "missing $target after unzip" >&2; exit 1; }
fi
sudo rm -rf /work/__MACOSX
sudo find /work -name .DS_Store -delete
sudo chown -R root:root /work
sudo chmod -R a+rX,go-w /work
du -sh /work/*
# Dataset similarity runtime for /prompt (offline on review boxes); rebuild_runner_image.sh copies the script here.
if [ ! -f /opt/simcheck/model/model.onnx ]; then bash /tmp/setup_similarity.sh; fi
echo RUNNER_TEMPLATE_READY
