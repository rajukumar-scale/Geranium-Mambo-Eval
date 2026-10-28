#!/usr/bin/env bash
# One-time VM setup for the HarmonyGames Eval Portal (Ubuntu 22.04 / 24.04). Run as root:
#   sudo DOMAIN=eval.example.com bash deploy/setup_vm.sh   (or DOMAIN=auto for <ip>.sslip.io)
# Re-running is safe: it skips finished steps. Afterwards put the keys in /opt/eval-portal/.env
# (LITELLM_API_KEY, REDASH_API_KEY, SLACK_WEBHOOK_URL) and `systemctl restart eval-portal-meter eval-portal`.
set -euo pipefail

APP_DIR=/opt/eval-portal
APP_USER=evalportal
DATA_DIR=/srv/eval-data
BASE_LINK=/srv/eval-base
ZIP_URL=${ZIP_URL:-https://static.remotasks.com/uploads/6a5595596cf3031de47f0ea2}
DOMAIN=${DOMAIN:-}
# PUBLIC_URL: the platform already serves HTTPS for you (e.g. boat.dev: https://<id>-8000.on.boat.dev).
# The portal then listens on 127.0.0.1:8000 and Caddy is skipped.
PUBLIC_URL=${PUBLIC_URL:-}
# DOMAIN=auto: no domain needed, use <public-ip>.sslip.io (resolves to this VM; Caddy still gets real HTTPS).
if [ "$DOMAIN" = "auto" ]; then
  DOMAIN="$(curl -fsS https://api.ipify.org | tr . -).sslip.io"
  echo "using $DOMAIN"
fi
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

log() { printf '\n== %s\n' "$*"; }

log "packages"
apt-get update -q
apt-get install -y -q python3 python3-venv unzip curl rsync ca-certificates
# Keep a preinstalled Docker (e.g. docker-ce on boat.dev); installing docker.io over it conflicts.
command -v docker >/dev/null || apt-get install -y -q docker.io
systemctl enable --now docker

log "app user and folders"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
usermod -aG docker "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
  rsync -a --delete --exclude .env --exclude data/ --exclude .venv/ --exclude __pycache__/ "$SRC_DIR/" "$APP_DIR/"
fi
chown -R "$APP_USER:$APP_USER" "$DATA_DIR"

log "eval bundle + universes"
# Skip when a release is already installed (re-runs); use update_eval.sh to refresh it.
[ -e "$BASE_LINK" ] || bash "$APP_DIR/deploy/update_eval.sh" --universes

log "runner image"
docker build -t eval-runner:latest "$APP_DIR/runner"

log "python environment"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

log ".env"
ENV="$APP_DIR/.env"
if [ ! -f "$ENV" ]; then
  cp "$APP_DIR/.env.example" "$ENV"
  gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
  sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=$(gen)|; s|^PROOF_SECRET=.*|PROOF_SECRET=$(gen)|; s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$(gen | cut -c1-16)|" "$ENV"
fi
# Runs get their own docker network; firewall.sh lets them reach only the proxy (no internet).
# (An --internal network can't reach the host at all on Docker 29, so it's a normal bridge + rules.)
if docker network inspect evalnet --format '{{.Internal}}' 2>/dev/null | grep -q true; then docker network rm evalnet >/dev/null; fi
docker network inspect evalnet >/dev/null 2>&1 || \
  docker network create --subnet 172.30.0.0/24 --gateway 172.30.0.1 -o com.docker.network.bridge.name=evalbr0 evalnet >/dev/null
bash "$APP_DIR/deploy/firewall.sh"
NET=evalnet; GATEWAY=172.30.0.1
python3 -m http.server 8001 --bind "$GATEWAY" >/dev/null 2>&1 & PROBE=$!; sleep 1
docker run --rm --network evalnet eval-runner:latest curl -s -m 5 -o /dev/null "http://$GATEWAY:8001/" \
  && echo "runs reach the proxy at $GATEWAY:8001" || echo "WARNING: runs can't reach the proxy at $GATEWAY:8001"
docker run --rm --network evalnet eval-runner:latest curl -s -m 5 -o /dev/null https://example.com \
  && echo "WARNING: runs can still reach the internet" || echo "runs have no internet access"
kill $PROBE 2>/dev/null || true
# The proxy listens on all interfaces: containers use $GATEWAY, boat review boxes use its public relay.
# Outside access is limited by the token check (and the platform firewall).
sed -i "s|^DOCKER_NETWORK=.*|DOCKER_NETWORK=${NET}|; s|^PROXY_HOST=.*|PROXY_HOST=0.0.0.0|; s|^PROXY_URL_FOR_RUNNER=.*|PROXY_URL_FOR_RUNNER=http://${GATEWAY}:8001|" "$ENV"
if [ -n "$PUBLIC_URL" ]; then
  sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${PUBLIC_URL%/}|; s|^HOST=.*|HOST=127.0.0.1|; s|^COOKIE_SECURE=.*|COOKIE_SECURE=1|; s|^TRUST_PROXY=.*|TRUST_PROXY=0|" "$ENV"
elif [ -n "$DOMAIN" ]; then
  sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://${DOMAIN}|" "$ENV"
else  # no domain: serve plain HTTP on port 8000 (put it behind your own load balancer / VPN)
  IP=$(hostname -I | awk '{print $1}')
  sed -i "s|^HOST=.*|HOST=0.0.0.0|; s|^COOKIE_SECURE=.*|COOKIE_SECURE=0|; s|^TRUST_PROXY=.*|TRUST_PROXY=0|; s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=http://${IP}:8000|" "$ENV"
fi
chown "$APP_USER:$APP_USER" "$ENV"; chmod 600 "$ENV"
chown -R root:root "$APP_DIR"; chown "$APP_USER:$APP_USER" "$ENV"

log "systemd service"
install -m 644 "$APP_DIR/deploy/eval-portal.service" /etc/systemd/system/eval-portal.service
install -m 644 "$APP_DIR/deploy/eval-portal-meter.service" /etc/systemd/system/eval-portal-meter.service
install -m 644 "$APP_DIR/deploy/eval-portal-firewall.service" /etc/systemd/system/eval-portal-firewall.service
install -m 644 "$APP_DIR/deploy/eval-portal-firewall.timer" /etc/systemd/system/eval-portal-firewall.timer
systemctl daemon-reload
systemctl enable eval-portal eval-portal-meter
systemctl enable --now eval-portal-firewall.timer

if [ -n "$DOMAIN" ] && [ -z "$PUBLIC_URL" ]; then
  log "Caddy (HTTPS for $DOMAIN)"
  if ! command -v caddy >/dev/null; then
    apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https gnupg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -q && apt-get install -y -q caddy
  fi
  sed "s|{\$DOMAIN}|$DOMAIN|" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
  systemctl reload caddy || systemctl restart caddy
fi

log "done"
missing=$(grep -E '^(LITELLM_API_KEY|REDASH_API_KEY)=$' "$ENV" || true)
if [ -n "$missing" ]; then
  echo "Fill these in $ENV, then run: systemctl restart eval-portal-meter eval-portal"
  echo "$missing"
else
  systemctl restart eval-portal-meter eval-portal
fi
echo "Admin password: $(grep '^ADMIN_PASSWORD=' "$ENV" | cut -d= -f2)  (admin page: /admin)"
