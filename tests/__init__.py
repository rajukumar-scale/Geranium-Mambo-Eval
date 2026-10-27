"""Portal tests. Run all of them with deploy/run_tests.sh (GitHub runs them on every push and pull request)."""

import os

os.environ.setdefault("PORTAL_ENV_FILE", os.devnull)  # never read the server's real .env in tests
