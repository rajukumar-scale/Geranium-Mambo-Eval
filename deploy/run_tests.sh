#!/bin/bash
# Run the portal's tests. Used by GitHub on every push and pull request, by the auto-deploy on the server before it
# deploys (PYTHON=/opt/eval-portal/.venv/bin/python), and by hand: deploy/run_tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${PYTHON:-python3}" -m unittest discover -s tests -t . "$@"
