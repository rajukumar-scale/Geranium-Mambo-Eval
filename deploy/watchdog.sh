#!/usr/bin/env bash
# Outside watchdog for the portal's server box (2026-11-05): run by .github/workflows/watchdog.yml, off the box, so it
# still works when the box is down. If the portal doesn't answer and boat has the box stopped, resume it with
# auto-stop off and tell Slack. A box that is running but not answering is only reported: power-cycling it would cut
# every running review, so a person decides that.
# Needs BOAT_API_KEY (scoped to the box) and SLACK_WEBHOOK_URL; BOX and PORTAL have defaults.
set -uo pipefail
BOX=${BOX:-bx_7r4qxme3}
PORTAL=${PORTAL:-https://box-node-df450a5ccdfeb2c5-8000.on.boat.dev}

slack() {
  [ -n "${SLACK_WEBHOOK_URL:-}" ] || { echo "no Slack webhook: $1"; return; }
  python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$1" |
    curl -s -o /dev/null -X POST -H 'Content-Type: application/json' --data @- "$SLACK_WEBHOOK_URL"
}

up() {  # the portal itself answers (not boat's "archived preview" fallback page)
  local code
  code=$(curl -s -o /tmp/status.json -w '%{http_code}' --max-time 20 "$PORTAL/api/status") || return 1
  [ "$code" = 200 ] && python3 -c 'import json; json.load(open("/tmp/status.json"))["overall"]' 2>/dev/null
}

if up; then echo "portal is up"; exit 0; fi
sleep 60
if up; then echo "portal is up (second try)"; exit 0; fi

curl -fsSL https://boat.dev/install | sh >/dev/null
export PATH="$HOME/.ascii/bin:$PATH" BOAT_NO_UPDATE=1
printf '%s' "$BOAT_API_KEY" | boat login --key-stdin >/dev/null || { slack ":rotating_light: Eval portal is down and the watchdog couldn't sign in to boat. Check $BOX."; exit 1; }

state() { boat info "$BOX" --json 2>/dev/null | python3 -c 'import json,sys; s=json.load(sys.stdin)["sandbox"]; print(s["state"], s.get("stoppedAt") or "")'; }
read -r STATE STOPPED_AT <<<"$(state)"
echo "portal down; box $BOX is ${STATE:-unknown}"

for _ in $(seq 1 40); do  # a stop or a start in progress: wait for it to settle (up to 10 min)
  case "$STATE" in stopping|starting|cloning|provisioned) sleep 15; read -r STATE STOPPED_AT <<<"$(state)";; *) break;; esac
done

case "$STATE" in
  stopped|archived)
    boat resume "$BOX" --no-auto-stop --json >/tmp/resume.log 2>&1
    boat extend "$BOX" --no-auto-stop --json >/dev/null 2>&1
    for _ in $(seq 1 30); do up && break; sleep 20; done
    if up; then
      slack ":rotating_light: The eval portal's server box \`$BOX\` was stopped by boat (at ${STOPPED_AT:-an unknown time}). The watchdog resumed it with auto-stop off and the portal is answering again. Reviews running at the time may have failed."
    else
      slack ":rotating_light: The eval portal's server box \`$BOX\` was stopped (at ${STOPPED_AT:-an unknown time}). The watchdog resumed it, but the portal isn't answering yet. Check it: \`boat info $BOX\`."
    fi ;;
  idle|running|ready)
    slack ":warning: The eval portal isn't answering, but its box \`$BOX\` is $STATE in boat. Not restarting it (that would cut running reviews). Check it: \`boat ssh $BOX\`, then \`systemctl status eval-portal\`." ;;
  *)
    slack ":warning: The eval portal isn't answering and boat says its box \`$BOX\` is '${STATE:-unknown}'. Check it: \`boat info $BOX\`." ;;
esac
