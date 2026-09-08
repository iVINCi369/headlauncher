"""One-button updates for headscale and the bot.

Runs `docker compose` in a detached one-shot container (docker:cli) so that the bot
can update *itself* without killing the command half-way. The stack directory is
mounted inside the bot at the same path as on the host, so bind mounts resolve.
"""
import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx

STACK = Path(os.environ.get("STACK_DIR", "/opt/headlauncher"))
ENV = STACK / ".env"
DB = STACK / "data" / "headscale" / "db.sqlite"
BACKUPS = STACK / "data" / "backups"
CLI_IMAGE = os.environ.get("DOCKER_CLI_IMAGE", "docker:27-cli")
GH_RELEASES = "https://api.github.com/repos/juanfont/headscale/releases/latest"


# ----------------------------------------------------------------- .env helpers
def env_read() -> dict:
    d = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def env_set(**kv):
    lines = ENV.read_text().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if k in kv:
            lines[i] = f"{k}={kv[k]}"
            seen.add(k)
    for k, v in kv.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    ENV.write_text("\n".join(lines) + "\n")


# ----------------------------------------------------------------- versions
async def latest_headscale() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(GH_RELEASES, headers={"Accept": "application/vnd.github+json"})
            tag = r.json().get("tag_name", "")
            return tag if re.fullmatch(r"v\d+\.\d+\.\d+", tag) else None
    except Exception:
        return None


def current_headscale() -> str:
    return env_read().get("HEADSCALE_VERSION") or ("external" if os.environ.get("MODE") == "attach" else "?")


def bot_version() -> str:
    p = Path(__file__).with_name("VERSION")
    return p.read_text().strip() if p.exists() else os.environ.get("BOT_VERSION", "dev")


def newer(a: str, b: str) -> bool:
    """True if a > b (both like v0.29.3)."""
    try:
        return tuple(int(x) for x in a[1:].split(".")) > tuple(int(x) for x in b[1:].split("."))
    except Exception:
        return False


# ----------------------------------------------------------------- backup
def backup_db(tag: str) -> Path | None:
    if not DB.exists():
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dst = BACKUPS / f"db-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite"
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(dst)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    for k in ("noise_private.key", "derp_server_private.key"):
        f = DB.with_name(k)
        if f.exists():
            shutil.copy2(f, BACKUPS / f"{k}.{tag}")
    # keep last 10
    for old in sorted(BACKUPS.glob("db-*.sqlite"))[:-10]:
        old.unlink(missing_ok=True)
    return dst


# ----------------------------------------------------------------- docker one-shot
def _docker(*args, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def oneshot(shell_cmd: str, rm: bool = False) -> str:
    """Start a detached docker:cli container running `shell_cmd` in the stack dir; return its id.
    rm=False by default: the exit code must survive until wait_oneshot() has read it (it removes the container)."""
    p = _docker("run", "-d", *(["--rm"] if rm else []), "--label", "headlauncher=oneshot",
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-v", f"{STACK}:{STACK}", "-w", str(STACK),
                CLI_IMAGE, "sh", "-c", shell_cmd)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-500:])
    return p.stdout.strip()


async def wait_oneshot(cid: str, timeout: int = 600) -> tuple[int, str]:
    """Wait for the one-shot container: returns (exit_code, logs) and removes it. -1 if it vanished."""
    deadline = time.time() + timeout
    logs = ""
    try:
        while time.time() < deadline:
            p = await asyncio.to_thread(_docker, "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", cid)
            if p.returncode != 0:
                return -1, logs + "\n[container vanished]"
            running, code = p.stdout.split()
            if running == "false":
                lp = await asyncio.to_thread(_docker, "logs", cid)
                return int(code), (lp.stdout + lp.stderr)[-4000:]
            await asyncio.sleep(3)
        lp = await asyncio.to_thread(_docker, "logs", cid)
        return -2, (lp.stdout + lp.stderr)[-4000:] + "\n[timeout]"
    finally:
        await asyncio.to_thread(_docker, "rm", "-f", cid)


async def compose_up(service: str, pull: bool = True) -> tuple[int, str]:
    cmd = (f"docker compose pull {service} && " if pull else "") + f"docker compose up -d --no-deps {service} && echo exit0"
    cid = await asyncio.to_thread(oneshot, cmd)
    return await wait_oneshot(cid)


# ----------------------------------------------------------------- high level
async def update_headscale(new: str, health_url: str) -> tuple[bool, str]:
    cur = current_headscale()
    backup_db(cur)
    env_set(HEADSCALE_PREV=cur, HEADSCALE_VERSION=new)
    code, logs = await compose_up("headscale")
    if code == 0 and await _healthy(health_url):
        return True, logs
    env_set(HEADSCALE_VERSION=cur)
    await compose_up("headscale", pull=False)
    return False, logs


async def rollback_headscale(health_url: str) -> tuple[bool, str]:
    e = env_read()
    prev = e.get("HEADSCALE_PREV")
    if not prev:
        return False, "no previous version"
    backup_db(e.get("HEADSCALE_VERSION", "cur"))
    env_set(HEADSCALE_VERSION=prev, HEADSCALE_PREV=e.get("HEADSCALE_VERSION", ""))
    code, logs = await compose_up("headscale", pull=False)
    return code == 0 and await _healthy(health_url), logs


def update_bot_detached():
    """Fire and forget: the bot itself gets recreated by the one-shot container."""
    return oneshot("sleep 2 && docker compose pull bot && docker compose up -d --no-deps bot", rm=True)


async def _healthy(url: str, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=5) as c:
        while time.time() < deadline:
            try:
                r = await c.get(url)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(3)
    return False
