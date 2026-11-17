#!/usr/bin/env bash
# Run by eval-similarity-refresh.service (hourly): add newly submitted prompts to the dataset-similarity library
# in the live eval release. Reads REDASH_URL / REDASH_API_KEY from the portal's .env like the other deploy scripts
# (first word of the value, so trailing comments are dropped).
set -euo pipefail
env_val() { grep -E "^$1=" /opt/eval-portal/.env 2>/dev/null | tail -1 | cut -d= -f2- | awk '{print $1}'; }
export REDASH_URL="$(env_val REDASH_URL)" REDASH_API_KEY="$(env_val REDASH_API_KEY)"
SIM_QID="$(env_val HG_SIM_QUERY_ID)"
[ -n "$SIM_QID" ] && export HG_SIM_QUERY_ID="$SIM_QID"
exec /opt/simcheck/bin/python /srv/eval-base/Evals/tools/hg_similarity.py refresh
