#!/usr/bin/env bash
# HeadLauncher installer: self-hosted Tailscale (headscale) managed from Telegram.
#   curl -fsSL https://raw.githubusercontent.com/iVINCi369/headlauncher/main/install.sh | bash
# Asks three things: domain, bot token, (optionally) your Telegram ID. Everything else is buttons.
set -euo pipefail

REPO="${HL_REPO:-https://github.com/iVINCi369/headlauncher.git}"
BRANCH="${HL_BRANCH:-main}"
DIR="${HL_DIR:-/opt/headlauncher}"
HEADSCALE_VERSION="${HL_HEADSCALE_VERSION:-v0.29.3}"

c() { printf "\033[%sm%s\033[0m\n" "$1" "$2"; }
say()  { c "1;36" "▶ $*"; }
ok()   { c "1;32" "✔ $*"; }
warn() { c "1;33" "! $*"; }
die()  { c "1;31" "✖ $*"; exit 1; }
ask()  { local v; printf "\033[1m%s\033[0m" "$1"; read -r v </dev/tty; echo "$v"; }

[ "$(id -u)" = 0 ] || die "run as root (sudo -i)"
command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl; }

# ---------------------------------------------------------------- docker
if ! command -v docker >/dev/null; then
  say "Installing Docker"
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
docker compose version >/dev/null 2>&1 || die "docker compose plugin missing — install docker-compose-plugin"
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }

# ---------------------------------------------------------------- code
if [ -d "$DIR/.git" ]; then
  say "Updating $DIR"; git -C "$DIR" pull -q --ff-only
elif [ -f "$DIR/docker-compose.yml" ]; then
  warn "using existing files in $DIR (not a git checkout — no auto-update)"
else
  say "Cloning into $DIR"; git clone -q -b "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

if [ -f .env ]; then
  warn ".env exists — already installed. Re-running only refreshes code and containers."
  docker compose up -d --remove-orphans
  ok "done. Bot: https://t.me/$(grep ^BOT_USERNAME .env | cut -d= -f2)"
  exit 0
fi

# ---------------------------------------------------------------- questions
echo
c "1" "HeadLauncher setup — three questions."
echo
DOMAIN=$(ask "1/3  Domain pointing to this server (e.g. vpn.example.com): ")
[[ "$DOMAIN" =~ ^[a-zA-Z0-9.-]+$ ]] || die "bad domain"
PUB_IP=$(curl -fsS -4 https://api.ipify.org || true)
DNS_IP=$(getent ahostsv4 "$DOMAIN" | awk '{print $1; exit}' || true)
if [ -n "$PUB_IP" ] && [ "$PUB_IP" != "$DNS_IP" ]; then
  warn "$DOMAIN resolves to '${DNS_IP:-nothing}', this server is $PUB_IP. TLS will fail until DNS is right."
  [ "$(ask "continue anyway? [y/N] ")" = y ] || exit 1
fi

BOT_TOKEN=$(ask "2/3  Telegram bot token from @BotFather: ")
ME=$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getMe" || true)
BOT_USERNAME=$(echo "$ME" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')
[ -n "$BOT_USERNAME" ] || die "token rejected by Telegram"
ok "bot @$BOT_USERNAME"

ADMIN_ID=$(ask "3/3  Your Telegram user ID (Enter to skip — you will claim admin by a link): ")
[[ -z "$ADMIN_ID" || "$ADMIN_ID" =~ ^[0-9]+$ ]] || die "ID must be a number"
CLAIM_CODE=$(tr -dc a-z0-9 </dev/urandom | head -c 10)

LANG_DEFAULT=en
case "${LANG:-}" in ru*) LANG_DEFAULT=ru;; esac

# ---------------------------------------------------------------- files
say "Writing configuration"
mkdir -p data/headscale data/config data/caddy data/bot data/ssh data/backups
sed -e "s/__DOMAIN__/$DOMAIN/" -e "s/__BASE_DOMAIN__/vpn.local/" headscale/config.template.yaml > data/config/headscale.yaml
sed -e "s/__USER__/admin/g" headscale/acl.hujson > data/config/acl.hujson
chown -R 1000:1000 data/headscale data/config 2>/dev/null || true
cat > .env <<EOF
DOMAIN=$DOMAIN
BOT_TOKEN=$BOT_TOKEN
BOT_USERNAME=$BOT_USERNAME
ADMINS=$ADMIN_ID
CLAIM_CODE=$CLAIM_CODE
HS_API=
HS_USER=admin
HEADSCALE_VERSION=$HEADSCALE_VERSION
BOT_VERSION=latest
STACK_DIR=$DIR
LANG_DEFAULT=$LANG_DEFAULT
EXIT_HOSTS=
EOF
chmod 600 .env

# ---------------------------------------------------------------- start headscale + caddy
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

# ---------------------------------------------------------------- bot
say "Starting the bot"
if [ -z "${HL_NO_PULL:-}" ] && docker compose pull bot >/dev/null 2>&1; then :; else
  warn "no prebuilt image — building locally (takes a minute)"; docker compose build -q bot
fi
docker compose up -d bot
sleep 3
docker compose ps --format '{{.Name}} {{.Status}}' | sed 's/^/   /'

# ---------------------------------------------------------------- done
echo
line=$(printf '=%.0s' $(seq 1 56))
c "1;32" "$line"
c "1;32" "  HeadLauncher is installed."
c "1;32" "$line"
echo
if [ -n "$ADMIN_ID" ]; then
  echo "  Open the bot and press /start:   https://t.me/$BOT_USERNAME"
else
  echo "  Tap this link to become admin (one-time):"
  echo
  c "1;33" "     https://t.me/$BOT_USERNAME?start=$CLAIM_CODE"
  echo
fi
echo "  The bot will send your first connection key with a QR code."
echo "  Panel (inside Telegram): https://$DOMAIN/tg/"
echo "  Stack dir: $DIR   ·   logs: docker compose -f $DIR/docker-compose.yml logs -f"
echo
