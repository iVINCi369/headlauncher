# HeadLauncher

**Self-hosted Tailscale (headscale), managed from Telegram.** One command installs everything; after that you only press buttons.

[Русская версия](README.ru.md)

```
curl -fsSL https://raw.githubusercontent.com/iVINCi369/headlauncher/main/install.sh | sudo bash
```

The installer asks three things — your domain, a bot token from [@BotFather](https://t.me/BotFather), and (optionally) your Telegram ID — then starts `headscale + caddy + bot` with Docker Compose, issues a TLS certificate, and prints a one-time link. Tap it: you become the admin and receive your **first connection key with a QR code** right in the chat.

## What you get

- **Telegram Mini App panel** — devices (rename, tags, routes, exit node, log out, delete), pre-auth keys with QR, users, status.
- **Share access in one tap** — the *Share* button opens Telegram's chat picker with ready-made instructions for a new person.
- **Register by link** — paste the `/register/…` link from `tailscale up` into the chat.
- **Exit node** — one button for hosts the bot can reach over SSH (`EXIT_HOSTS`); tags `exit`/`router` auto-approve routes for everything else.
- **Files** — send a document to the bot → a [sendme](https://github.com/n0-computer/sendme) (iroh) ticket + QR; send a ticket → the bot fetches the file. P2P, no size limit beyond Telegram's 50 MB for delivery into chat.
- **Updates** — *Updates* button: headscale (with automatic database backup and rollback) and the bot itself.
- **Embedded DERP relay** — works behind strict NAT without relying on public relays.
- English / Russian, picked from your Telegram language (`/lang` to switch).

## Already running headscale? Attach mode

The same command detects an existing headscale (binary, systemd service or container) and offers to install **only the bot**, plugged into it. It reads `server_url`/`listen_addr` from your config, creates an API key for the bot, asks for the bot token, starts the bot on `127.0.0.1:8090` and — if nginx serves your domain — adds the `location /tg/` block itself (with a backup and `nginx -t`). For Caddy/Traefik it prints the one route you need to add. Your headscale, its database and your ACL are not touched; the *Updates* button then covers only the bot.

Non-interactive:

```
HL_MODE=attach HL_HS_URL=https://vpn.example.com HL_HS_API_URL=http://127.0.0.1:8080 HL_BOT_TOKEN=… sudo -E bash install.sh
```

## Requirements

Full mode: a Linux server with a public IPv4, ports 80/443 (TCP) and 3478 (UDP) open, and a domain pointing at it. 1 GB RAM is plenty.
Attach mode: Docker, a working headscale ≥ 0.23 with its API reachable from the host, and a reverse proxy in front of it.

## Layout

```
/opt/headlauncher
├── .env                  # your settings (chmod 600)
├── docker-compose.yml         # full mode: headscale + caddy + bot
├── docker-compose.attach.yml  # attach mode: bot only (host network)
├── caddy/Caddyfile
├── headscale/            # config template + default ACL
├── bot/                  # the Telegram bot (Python, aiogram + FastAPI)
└── data/                 # headscale db & keys, caddy certs, bot state, backups
```

Useful: `docker compose -f /opt/headlauncher/docker-compose.yml logs -f bot` · re-run `install.sh` to refresh code and containers.

## Security

The panel accepts only requests signed by Telegram (`initData` HMAC with the bot token) from admin IDs; every bot handler checks the sender. Only the admin(s) can use the bot. The API key for headscale never leaves the server.

## License

MIT
