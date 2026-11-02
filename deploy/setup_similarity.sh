#!/usr/bin/env bash
# Installs the dataset-similarity runtime into /opt/simcheck so Evals/tools/hg_similarity.py runs
# offline: a small venv (onnxruntime + tokenizers + numpy, ~125 MB) and the ONNX export of
# all-mpnet-base-v2 (model.onnx + tokenizer.json, ~436 MB, same scores as the PyTorch model).
# Run it on:
#   - the runner template box before `boat snapshot <box> hg-runner` (reviews run the check), and
#   - the main box, then enable eval-similarity-refresh.timer (it refreshes the library hourly).
set -euo pipefail
PREFIX=/opt/simcheck
REV=e8c3b32edf5434bc2275fc9bab85f82640a19130   # sentence-transformers/all-mpnet-base-v2, pinned
BASE=https://huggingface.co/sentence-transformers/all-mpnet-base-v2/resolve/$REV

sudo apt-get install -y -q python3-venv curl
sudo python3 -m venv "$PREFIX"
sudo "$PREFIX/bin/pip" install -q --upgrade pip
sudo "$PREFIX/bin/pip" install -q onnxruntime==1.31.1 tokenizers==0.23.3 numpy
sudo mkdir -p "$PREFIX/model"
sudo curl -fsSL --retry 5 -o "$PREFIX/model/model.onnx" "$BASE/onnx/model.onnx"
sudo curl -fsSL --retry 5 -o "$PREFIX/model/tokenizer.json" "$BASE/tokenizer.json"
( cd "$PREFIX/model" && sha256sum -c - ) <<'EOF'
74187b16d9c946fea252e120cfd7a12c5779d8b8b86838a2e4c56573c47941bd  model.onnx
b8be2c30ba5dd723a6d5ee26d013da103d5408d92ddcb23747622f9e48f1d842  tokenizer.json
EOF
sudo rm -rf "$PREFIX"/lib/python3*/site-packages/pip "$PREFIX"/lib/python3*/site-packages/pip-*
sudo chmod -R a+rX,go-w "$PREFIX"
"$PREFIX/bin/python" -c "import onnxruntime, tokenizers, numpy; print('runtime ok', onnxruntime.__version__)"
du -sh "$PREFIX"
echo SIMCHECK_READY
