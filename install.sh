#!/usr/bin/env bash
# HeadLauncher installer: self-hosted Tailscale (headscale) managed from Telegram.
#   curl -fsSL https://raw.githubusercontent.com/iVINCi369/headlauncher/main/install.sh | bash
#
# Two modes:
#   full   — clean server: installs headscale + caddy (TLS) + bot. Asks domain, bot token, your ID.
#   attach — headscale already runs here: installs only the bot and plugs it into it.
#            Detected automatically (headscale binary / service / container), or force with HL_MODE=attach.
# Non-interactive: HL_MODE=full HL_DOMAIN=… HL_BOT_TOKEN=… HL_ADMIN_ID= bash install.sh
#                  HL_MODE=attach HL_HS_URL=https://vpn.example.com HL_HS_API_URL=http://127.0.0.1:8080 HL_HS_API=… HL_BOT_TOKEN=… bash install.sh
set -euo pipefail

REPO="${HL_REPO:-https://github.com/iVINCi369/headlauncher.git}"
BRANCH="${HL_BRANCH:-main}"
DIR="${HL_DIR:-/opt/headlauncher}"
HEADSCALE_VERSION="${HL_HEADSCALE_VERSION:-v0.29.3}"
PANEL_PORT="${HL_PANEL_PORT:-8090}"

c() { printf "\033[%sm%s\033[0m\n" "$1" "$2"; }
say()  { c "1;36" "▶ $*"; }
ok()   { c "1;32" "✔ $*"; }
warn() { c "1;33" "! $*"; }
die()  { c "1;31" "✖ $*"; exit 1; }
ask()  { local v; printf "\033[1m%s\033[0m" "$1"; read -r v </dev/tty; echo "$v"; }
askd() { local v; v=$(ask "$1 [$2]: "); echo "${v:-$2}"; }          # with default
retry() { local n; for n in 1 2 3 4; do "$@" && return 0; warn "attempt $n failed, retrying…"; sleep 5; done; return 1; }

[ "$(id -u)" = 0 ] || die "run as root (sudo -i)"
command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl; }

# ---------------------------------------------------------------- docker
if ! command -v docker >/dev/null; then
  say "Installing Docker"
  retry sh -c 'curl -fsSL https://get.docker.com | sh >/dev/null' || die "Docker install failed"
fi
docker compose version >/dev/null 2>&1 || die "docker compose plugin missing — install docker-compose-plugin"
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }

# ---------------------------------------------------------------- code
export GIT_TERMINAL_PROMPT=0
if [ -d "$DIR/.git" ]; then
  say "Updating $DIR"; retry timeout 90 git -C "$DIR" pull -q --ff-only || warn "could not update code (GitHub unreachable) — continuing with the current files"
elif [ -f "$DIR/docker-compose.yml" ]; then
  warn "using existing files in $DIR (not a git checkout — no auto-update)"
else
  say "Cloning into $DIR"; retry timeout 120 git clone -q -b "$BRANCH" "$REPO" "$DIR" || die "could not clone $REPO"
fi
cd "$DIR"

if [ -f .env ]; then
  warn ".env exists — already installed. Re-running only refreshes code and containers."
  docker compose up -d --remove-orphans
  ok "done. Bot: https://t.me/$(grep ^BOT_USERNAME .env | cut -d= -f2)"
  exit 0
fi

# ---------------------------------------------------------------- mode
HS_BIN=$(command -v headscale || true)
HS_CT=$(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | awk '/headscale/{print $1; exit}' || true)
HSCLI=""
[ -n "$HS_BIN" ] && HSCLI="$HS_BIN"
[ -z "$HSCLI" ] && [ -n "$HS_CT" ] && HSCLI="docker exec -i $HS_CT headscale"
MODE=${HL_MODE:-}
if [ -z "$MODE" ]; then
  MODE=full
  if [ -n "$HSCLI" ] || systemctl is-active -q headscale 2>/dev/null; then
    echo
    warn "headscale is already running on this server (${HS_BIN:-container $HS_CT})."
    [ "$(askd "Attach the bot to it instead of installing a new headscale? [Y/n]" y)" = n ] || MODE=attach
  fi
fi
[[ "$MODE" = full || "$MODE" = attach ]] || die "HL_MODE must be full or attach"

# ---------------------------------------------------------------- questions
echo
c "1" "HeadLauncher setup ($MODE mode) — a few questions."
echo
if [ "$MODE" = full ]; then
  DOMAIN=${HL_DOMAIN:-$(ask "1/3  Domain pointing to this server (e.g. vpn.example.com): ")}
  [[ "$DOMAIN" =~ ^[a-zA-Z0-9.-]+$ ]] || die "bad domain"
  PUB_IP=$(curl -fsS -4 https://api.ipify.org || true)
  DNS_IP=$(getent ahostsv4 "$DOMAIN" | awk '{print $1; exit}' || true)
  if [ -n "$PUB_IP" ] && [ "$PUB_IP" != "$DNS_IP" ]; then
    warn "$DOMAIN resolves to '${DNS_IP:-nothing}', this server is $PUB_IP. TLS will fail until DNS is right."
    [ -n "${HL_DOMAIN:-}" ] || [ "$(ask "continue anyway? [y/N] ")" = y ] || exit 1
  fi
  HS_URL="https://$DOMAIN"
else
  # read what we can from the existing config
  CFG=""
  for f in /etc/headscale/config.yaml /etc/headscale/config.yml; do [ -f "$f" ] && CFG=$(cat "$f") && break; done
  [ -z "$CFG" ] && [ -n "$HS_CT" ] && CFG=$(docker exec "$HS_CT" cat /etc/headscale/config.yaml 2>/dev/null || true)
  cfg() { echo "$CFG" | sed -n "s/^$1:[[:space:]]*//p" | head -n1 | tr -d "\"' "; }
  DEF_URL=$(cfg server_url); DEF_URL=${DEF_URL:-https://vpn.example.com}
  LISTEN_ADDR=$(cfg listen_addr); LISTEN_ADDR=${LISTEN_ADDR:-127.0.0.1:8080}
  DEF_API="http://127.0.0.1:${LISTEN_ADDR##*:}"
  HS_URL=${HL_HS_URL:-$(askd "1/4  Public headscale URL (server_url)" "$DEF_URL")}
  HS_URL=${HS_URL%/}
  [[ "$HS_URL" =~ ^https://[a-zA-Z0-9.-]+(:[0-9]+)?$ ]] || die "URL must look like https://vpn.example.com"
  DOMAIN=${HS_URL#https://}; DOMAIN=${DOMAIN%%:*}
  HS_API_URL=${HL_HS_API_URL:-$(askd "2/4  headscale API address as seen from this host" "$DEF_API")}
  HS_API_URL=${HS_API_URL%/}
  if [ -n "${HL_HS_API:-}" ]; then HS_API=$HL_HS_API
  elif [ -n "$HSCLI" ]; then
    say "Creating an API key for the bot (10 years)"
    HS_API=$($HSCLI apikeys create --expiration 3650d 2>/dev/null | tail -n1 | tr -d '\r ' || true)
  fi
  [ -n "${HS_API:-}" ] || HS_API=$(ask "     API key (headscale apikeys create --expiration 3650d): ")
  USERS_JSON=$(curl -fsS -m 10 -H "Authorization: Bearer $HS_API" "$HS_API_URL/api/v1/user" 2>/dev/null) \
    || die "cannot reach $HS_API_URL/api/v1/user with that key — check the address and the key"
  DEF_USER=$(echo "$USERS_JSON" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | head -n1)
  HS_USER=${HL_HS_USER:-$(askd "     headscale user that owns new devices/keys" "${DEF_USER:-admin}")}
  echo "$USERS_JSON" | grep -q "\"name\":\"$HS_USER\"" || { $HSCLI users create "$HS_USER" >/dev/null 2>&1 || die "user $HS_USER does not exist"; }
  ok "headscale API ok, user $HS_USER"
  HEADSCALE_VERSION=$($HSCLI version 2>/dev/null | tr -d '\r' | head -n1 || true)
  Q3="3/4"; Q4="4/4"
fi

BOT_TOKEN=${HL_BOT_TOKEN:-$(ask "${Q3:-2/3}  Telegram bot token from @BotFather: ")}
ME=$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getMe" || true)
BOT_USERNAME=$(echo "$ME" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')
[ -n "$BOT_USERNAME" ] || die "token rejected by Telegram"
ok "bot @$BOT_USERNAME"

ADMIN_ID=${HL_ADMIN_ID-$(ask "${Q4:-3/3}  Your Telegram user ID (Enter to skip — you will claim admin by a link): ")}
[[ -z "$ADMIN_ID" || "$ADMIN_ID" =~ ^[0-9]+$ ]] || die "ID must be a number"
CLAIM_CODE=$(od -An -tx1 -N6 /dev/urandom | tr -d " \n")

LANG_DEFAULT=en
case "${LANG:-}" in ru*) LANG_DEFAULT=ru;; esac

# ---------------------------------------------------------------- files
say "Writing configuration"
mkdir -p data/bot data/ssh data/backups
if [ "$MODE" = full ]; then
  mkdir -p data/headscale data/config data/caddy
  sed -e "s/__DOMAIN__/$DOMAIN/" -e "s/__BASE_DOMAIN__/vpn.local/" headscale/config.template.yaml > data/config/headscale.yaml
  sed -e "s/__USER__/admin/g" headscale/acl.hujson > data/config/acl.hujson
  chown -R 1000:1000 data/headscale data/config 2>/dev/null || true
  HS_API=""; HS_USER=admin
fi
cat > .env <<EOF
MODE=$MODE
DOMAIN=$DOMAIN
HS_URL=$HS_URL
BOT_TOKEN=$BOT_TOKEN
BOT_USERNAME=$BOT_USERNAME
ADMINS=$ADMIN_ID
CLAIM_CODE=$CLAIM_CODE
HS_API=$HS_API
HS_USER=$HS_USER
HEADSCALE_VERSION=$HEADSCALE_VERSION
BOT_VERSION=latest
STACK_DIR=$DIR
LANG_DEFAULT=$LANG_DEFAULT
EXIT_HOSTS=
EOF
if [ "$MODE" = attach ]; then
  cat >> .env <<EOF
COMPOSE_FILE=docker-compose.attach.yml
HS_API_URL=$HS_API_URL
LISTEN=127.0.0.1:$PANEL_PORT
EOF
fi
chmod 600 .env

# ---------------------------------------------------------------- full: start headscale + caddy
if [ "$MODE" = full ]; then
  say "Starting headscale and caddy (TLS certificate is issued automatically)"
  docker compose up -d headscale caddy
  for i in $(seq 1 30); do
    docker compose exec -T headscale headscale health >/dev/null 2>&1 && break
    sleep 2; [ "$i" = 30 ] && die "headscale did not become healthy: docker compose logs headscale"
  done
  docker compose exec -T headscale headscale users create admin >/dev/null 2>&1 || true
  HS_API=$(docker compose exec -T headscale headscale apikeys create --expiration 3650d | tail -n1 | tr -d '\r')
  [ -n "$HS_API" ] || die "could not create API key"
  sed -i "s|^HS_API=.*|HS_API=$HS_API|" .env
  ok "headscale $HEADSCALE_VERSION is up"
fi

# ---------------------------------------------------------------- bot
say "Starting the bot"
if [ -z "${HL_NO_PULL:-}" ] && docker compose pull bot >/dev/null 2>&1; then :; else
  warn "no prebuilt image — building locally (takes a minute)"; docker compose build -q bot
fi
docker compose up -d bot
sleep 3
docker compose ps --format '{{.Name}} {{.Status}}' | sed 's/^/   /'

# ---------------------------------------------------------------- attach: reverse proxy for the panel
PROXY_NOTE=""
if [ "$MODE" = attach ]; then
  SNIPPET="    location /tg/ { proxy_pass http://127.0.0.1:$PANEL_PORT; proxy_set_header Host \$host; proxy_set_header X-Forwarded-Proto https; }"
  NGX=$(grep -lsE "server_name[^;]*\b$DOMAIN\b" /etc/nginx/sites-enabled/* /etc/nginx/conf.d/* 2>/dev/null | head -n1 || true)
  if [ -n "$NGX" ] && ! grep -q "location /tg/" "$NGX"; then
    echo
    say "nginx serves $DOMAIN in $NGX — the panel needs one location block:"
    echo "$SNIPPET"
    if [ -n "${HL_PROXY_AUTO:-}" ] || [ "$(askd "Add it automatically (backup is kept, nginx -t before reload)? [Y/n]" y)" != n ]; then
      cp "$NGX" "$NGX.bak-headlauncher"
      awk -v s="$SNIPPET" -v d="$DOMAIN" '{print} $1=="server_name" && index($0,d){print s}' "$NGX.bak-headlauncher" > "$NGX"
      if nginx -t >/dev/null 2>&1 && (systemctl reload nginx 2>/dev/null || nginx -s reload); then
        ok "nginx: /tg/ → bot"
      else
        cp "$NGX.bak-headlauncher" "$NGX"; PROXY_NOTE=nginx
        warn "nginx -t failed — file restored, add the block by hand"
      fi
    else PROXY_NOTE=nginx; fi
  elif [ -n "$NGX" ]; then ok "nginx already has /tg/"
  else PROXY_NOTE=generic; fi
fi

# ---------------------------------------------------------------- done
echo
line=$(printf '=%.0s' $(seq 1 56))
c "1;32" "$line"
c "1;32" "  HeadLauncher is installed."
c "1;32" "$line"
echo
if [ -n "$PROXY_NOTE" ]; then
  c "1;33" "  ONE MANUAL STEP: forward https://$DOMAIN/tg/ to the bot on 127.0.0.1:$PANEL_PORT"
  echo
  echo "  nginx (inside the server { } block for $DOMAIN):"
  echo "$SNIPPET"
  echo
  echo "  Caddy (inside the site block for $DOMAIN, before the headscale reverse_proxy):"
  echo "    handle /tg/* { reverse_proxy 127.0.0.1:$PANEL_PORT }"
  echo
  echo "  Traefik / other: route path prefix /tg/ of $DOMAIN to 127.0.0.1:$PANEL_PORT."
  echo "  The bot works in chat right away; the panel button needs this route."
  echo
fi
if [ -n "$ADMIN_ID" ]; then
  echo "  Open the bot and press /start:   https://t.me/$BOT_USERNAME"
else
  echo "  Tap this link to become admin (one-time):"
  echo
  c "1;33" "     https://t.me/$BOT_USERNAME?start=$CLAIM_CODE"
  echo
fi
echo "  The bot will send your first connection key with a QR code."
echo "  Panel (inside Telegram): $HS_URL/tg/"
echo "  Stack dir: $DIR   ·   logs: cd $DIR && docker compose logs -f"
echo
