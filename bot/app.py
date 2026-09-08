#!/usr/bin/env python3
"""HeadLauncher bot: Telegram bot + Mini App backend for headscale, with sendme file transfer
and one-button updates. Configuration comes from the environment (.env of the stack)."""
import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import pty
import re
import shutil
import signal
import subprocess
import time
import urllib.parse
import uuid
from pathlib import Path

import segno
import uvicorn
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, WebAppInfo)
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

import updater
from hs import HS
from i18n import pick, t

log = logging.getLogger("headlauncher")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE = Path(__file__).resolve().parent
E = os.environ.get

TOKEN = E("BOT_TOKEN", "")
DOMAIN = E("DOMAIN", "")
HS_URL = E("HS_URL", f"https://{DOMAIN}").rstrip("/")            # public, for clients
HS_API_URL = E("HS_API_URL", "http://headscale:8080").rstrip("/")  # inside the stack
HS_API = E("HS_API", "")
HS_USER = E("HS_USER", "admin")
WEBAPP_URL = E("WEBAPP_URL", HS_URL + "/tg/")
LISTEN = E("LISTEN", "0.0.0.0:8090")
SENDME = E("SENDME", "/usr/local/bin/sendme")
LANG_DEFAULT = E("LANG_DEFAULT", "en")
CLAIM_CODE = E("CLAIM_CODE", "")
MODE = E("MODE", "full")            # full = we run headscale; attach = existing headscale, bot only
MANAGED = MODE != "attach"
STACK = Path(E("STACK_DIR", "/opt/headlauncher"))
DATA = Path(E("DATA_DIR", str(STACK / "data" / "bot")))
FILES = DATA / "files"
OUT, IN = FILES / "out", FILES / "in"
for d in (OUT, IN):
    d.mkdir(parents=True, exist_ok=True)
ADMINS_FILE = DATA / "admins.json"
TG_MAX_SEND = 50 * 1024 * 1024
EXIT_HOSTS = {k.strip(): v.strip() for k, v in
              (x.split("=", 1) for x in E("EXIT_HOSTS", "").split(";") if "=" in x)}
EXIT_ROUTES = ["0.0.0.0/0", "::/0"]

hs = HS(HS_API_URL, HS_API)
bot = Bot(TOKEN)
dp = Dispatcher()
app = FastAPI(title="headlauncher")


# ----------------------------------------------------------------- admins
def load_admins() -> dict:
    d = {"ids": [], "claimed": False, "lang": {}}
    if ADMINS_FILE.exists():
        try:
            d.update(json.loads(ADMINS_FILE.read_text()))
        except Exception:
            pass
    for x in E("ADMINS", "").split(","):
        if x.strip().isdigit() and int(x) not in d["ids"]:
            d["ids"].append(int(x))
    return d


def save_admins(d: dict):
    ADMINS_FILE.write_text(json.dumps(d))


ADM = load_admins()


def is_admin(uid) -> bool:
    return int(uid) in ADM["ids"]


def lang_of(user: types.User | dict | None) -> str:
    code = getattr(user, "language_code", None) if user is not None and not isinstance(user, dict) else (user or {}).get("language_code")
    uid = getattr(user, "id", None) if not isinstance(user, dict) else (user or {}).get("id")
    return ADM["lang"].get(str(uid)) or pick(code, LANG_DEFAULT)


# ----------------------------------------------------------------- helpers
def qr_png_b64(text: str) -> str:
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=6, border=2)
    return base64.b64encode(buf.getvalue()).decode()


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_exp(iso: str) -> str:
    return (iso or "")[:16].replace("T", " ")


async def status_text(lang: str) -> str:
    ok = await hs.health(HS_API_URL)
    out = [t(lang, "status_ok" if ok else "status_bad")]
    try:
        nodes = await hs.nodes()
        out.append(t(lang, "nodes_online", on=sum(1 for n in nodes if n.get("online")), total=len(nodes)))
    except Exception as e:
        out.append(f"API: {e}")
    out.append(f"headscale {updater.current_headscale()} · bot {updater.bot_version()}")
    return "\n".join(out)


async def nodes_text(lang: str) -> str:
    nodes = await hs.nodes()
    if not nodes:
        return t(lang, "no_nodes")
    out = []
    for n in nodes:
        mark = "🟢" if n.get("online") else "⚪"
        extra = " (exit)" if "0.0.0.0/0" in (n.get("approvedRoutes") or []) else ""
        out.append(f"{mark} {n.get('givenName') or n.get('name')} - {', '.join(n.get('ipAddresses', []))}{extra}")
    out += ["", t(lang, "nodes_online", on=sum(1 for n in nodes if n.get("online")), total=len(nodes))]
    return "\n".join(out)


def key_text(lang: str, key: dict, reusable: bool) -> str:
    cmd = f"tailscale up --login-server {HS_URL} --authkey {key.get('key')}"
    return t(lang, "key_text", cmd=cmd, url=HS_URL, key=key.get("key"),
             kind=t(lang, "kind_reuse" if reusable else "kind_once"), exp=fmt_exp(key.get("expiration", "")))


async def send_key(chat_id: int, lang: str, reusable: bool, hours: int = 24):
    users = await hs.users()
    uid = next((u["id"] for u in users if u["name"] == HS_USER), users[0]["id"] if users else "1")
    k = await hs.key_create(uid, reusable, False, hours)
    cmd = f"tailscale up --login-server {HS_URL} --authkey {k.get('key')}"
    await bot.send_photo(chat_id, types.BufferedInputFile(base64.b64decode(qr_png_b64(cmd)), "qr.png"),
                         caption=key_text(lang, k, reusable), reply_markup=menu(lang))


# ----------------------------------------------------------------- sendme
class Share:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.size = path.stat().st_size if path.is_file() else sum(
            f.stat().st_size for f in path.rglob("*") if f.is_file())
        self.ticket = None
        self.proc = None
        self.reader = None
        self.started = time.time()
        self.log = ""

    async def start(self):
        master, slave = pty.openpty()  # sendme needs a TTY: without one its key listener panics
        self.proc = await asyncio.create_subprocess_exec(
            SENDME, "send", "--no-progress", str(self.path),
            cwd=str(OUT), stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        self.reader = asyncio.StreamReader()
        await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(self.reader), os.fdopen(master, "rb", 0))
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                line = await asyncio.wait_for(self.reader.readline(), 5)
            except asyncio.TimeoutError:
                if self.proc.returncode is not None:
                    break
                continue
            except OSError:
                break
            if not line:
                break
            s = line.decode(errors="replace").strip()
            self.log += s + "\n"
            m = re.search(r"sendme receive (\S+)", s)
            if m:
                self.ticket = m.group(1)
                asyncio.create_task(self._drain())
                return self.ticket
        await self.stop()
        raise RuntimeError("sendme gave no ticket:\n" + self.log[-800:])

    async def _drain(self):
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                self.log = (self.log + line.decode(errors="replace"))[-4000:]
        except OSError:
            pass

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def info(self):
        return {"name": self.name, "size": self.size, "human": human(self.size),
                "ticket": self.ticket, "started": int(self.started),
                "alive": self.proc is not None and self.proc.returncode is None}


shares: dict[str, Share] = {}
receives: dict[str, dict] = {}


async def share_path(path: Path) -> Share:
    old = shares.pop(path.name, None)
    if old:
        await old.stop()
    s = Share(path)
    await s.start()
    shares[path.name] = s
    return s


async def receive_ticket(ticket: str, chat_id: int | None, lang: str = "en"):
    rid = uuid.uuid4().hex[:8]
    d = IN / rid
    d.mkdir(parents=True, exist_ok=True)
    rec = {"id": rid, "ticket": ticket[:24] + "…", "state": "running", "started": int(time.time()),
           "files": [], "log": ""}
    receives[rid] = rec
    try:
        proc = await asyncio.create_subprocess_exec(
            SENDME, "receive", ticket, cwd=str(d),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 3600)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("timeout (1h)")
        rec["log"] = out.decode(errors="replace")[-2000:]
        if proc.returncode != 0:
            raise RuntimeError("sendme receive exit %s:\n%s" % (proc.returncode, rec["log"][-600:]))
        files = [f for f in d.rglob("*") if f.is_file()]
        rec["files"] = [{"name": str(f.relative_to(d)), "size": f.stat().st_size} for f in files]
        rec["state"] = "done"
        if chat_id:
            for f in files:
                if f.stat().st_size <= TG_MAX_SEND:
                    await bot.send_document(chat_id, FSInputFile(f), caption=f"📥 {f.name} ({human(f.stat().st_size)})")
                else:
                    await bot.send_message(chat_id, t(lang, "recv_big", name=f.name, size=human(f.stat().st_size), path=str(f)))
    except Exception as e:
        rec["state"] = "error"
        rec["error"] = str(e)
        if chat_id:
            await bot.send_message(chat_id, t(lang, "recv_fail", err=e))
    return rec


# ----------------------------------------------------------------- Mini App auth
def check_init_data(init_data: str) -> dict:
    try:
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    except Exception:
        raise HTTPException(401, "bad initData")
    d = dict(pairs)
    h = d.pop("hash", None)
    if not h:
        raise HTTPException(401, "no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, h):
        raise HTTPException(401, "bad signature")
    if time.time() - int(d.get("auth_date", "0")) > 86400:
        raise HTTPException(401, "initData expired, reopen the app")
    try:
        user = json.loads(d.get("user", "{}"))
    except Exception:
        user = {}
    if not is_admin(user.get("id", 0)):
        raise HTTPException(403, "not allowed")
    return user


async def auth(request: Request) -> dict:
    a = request.headers.get("Authorization", "")
    if not a.startswith("tma "):
        raise HTTPException(401, "no auth")
    return check_init_data(a[4:])


async def body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


@app.exception_handler(RuntimeError)
async def _rt(_, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


# ----------------------------------------------------------------- API
@app.get("/tg/")
@app.get("/tg/index.html")
async def index():
    return FileResponse(BASE / "webapp" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/tg/api/me")
async def api_me(user=Depends(auth)):
    return {"user": user, "hs_url": HS_URL, "exit_hosts": sorted(EXIT_HOSTS), "lang": lang_of(user),
            "versions": {"headscale": updater.current_headscale(), "bot": updater.bot_version()}}


@app.get("/tg/api/status")
async def api_status(user=Depends(auth)):
    latest = await updater.latest_headscale()
    cur = updater.current_headscale()
    return {"text": await status_text(lang_of(user)), "headscale": cur, "latest": latest, "managed": MANAGED,
            "update_available": MANAGED and bool(latest and updater.newer(latest, cur)),
            "prev": (updater.env_read().get("HEADSCALE_PREV") or None) if MANAGED else None,
            "bot": updater.bot_version()}


@app.get("/tg/api/nodes")
async def api_nodes(user=Depends(auth)):
    return {"nodes": await hs.nodes()}


@app.post("/tg/api/nodes/register")
async def api_register(b=Depends(body), user=Depends(auth)):
    r = await hs.register(b.get("user") or HS_USER, b.get("key", ""))
    return {"node": r.get("node")}


@app.post("/tg/api/nodes/{nid}/rename")
async def api_rename(nid: str, b=Depends(body), user=Depends(auth)):
    name = re.sub(r"[^a-z0-9-]", "-", (b.get("name") or "").lower()).strip("-")
    if not name:
        raise RuntimeError("empty name")
    return {"node": (await hs.rename(nid, name)).get("node")}


@app.post("/tg/api/nodes/{nid}/expire")
async def api_expire(nid: str, user=Depends(auth)):
    return {"node": (await hs.expire(nid)).get("node")}


@app.delete("/tg/api/nodes/{nid}")
async def api_delete(nid: str, user=Depends(auth)):
    await hs.delete(nid)
    return {"ok": True}


@app.post("/tg/api/nodes/{nid}/tags")
async def api_tags(nid: str, b=Depends(body), user=Depends(auth)):
    tags = [x if x.startswith("tag:") else "tag:" + x for x in b.get("tags", []) if x.strip()]
    return {"node": (await hs.tags(nid, tags)).get("node")}


@app.post("/tg/api/nodes/{nid}/routes")
async def api_routes(nid: str, b=Depends(body), user=Depends(auth)):
    return {"node": (await hs.approve_routes(nid, b.get("routes", []))).get("node")}


def exit_cmd(target: str, enable: bool):
    flag = "--advertise-exit-node" + ("" if enable else "=false")
    inner = f"tailscale set {flag} 2>/dev/null || sudo -n tailscale set {flag}"
    if target == "local":
        return ["bash", "-c", inner]
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new", target, inner]


@app.post("/tg/api/nodes/{nid}/exit")
async def api_exit(nid: str, b=Depends(body), user=Depends(auth)):
    enable = bool(b.get("enable", True))
    n = await hs.node(nid)
    name = n.get("givenName") or n.get("name")
    target = EXIT_HOSTS.get(name)
    if not target:
        raise RuntimeError(f"no SSH access to {name}. On the node: tailscale set --advertise-exit-node"
                           f"{'' if enable else '=false'} — then approve routes here.")
    p = await asyncio.to_thread(subprocess.run, exit_cmd(target, enable), capture_output=True, text=True, timeout=40)
    if p.returncode != 0:
        raise RuntimeError(f"{target}: {(p.stderr or p.stdout).strip()[-400:] or 'exit ' + str(p.returncode)}")
    approved = set(n.get("approvedRoutes") or [])
    if enable:
        for _ in range(10):
            n = await hs.node(nid)
            if all(r in (n.get("availableRoutes") or []) for r in EXIT_ROUTES):
                break
            await asyncio.sleep(1)
        approved |= set(EXIT_ROUTES)
    else:
        approved -= set(EXIT_ROUTES)
    return {"node": (await hs.approve_routes(nid, sorted(approved))).get("node")}


@app.get("/tg/api/users")
async def api_users(user=Depends(auth)):
    return {"users": await hs.users()}


@app.post("/tg/api/users")
async def api_user_create(b=Depends(body), user=Depends(auth)):
    return {"user": (await hs.user_create(b.get("name", ""))).get("user")}


@app.post("/tg/api/users/{uid}/rename")
async def api_user_rename(uid: str, b=Depends(body), user=Depends(auth)):
    return {"user": (await hs.user_rename(uid, b.get("name", ""))).get("user")}


@app.delete("/tg/api/users/{uid}")
async def api_user_delete(uid: str, user=Depends(auth)):
    await hs.user_delete(uid)
    return {"ok": True}


@app.get("/tg/api/keys")
async def api_keys(uid: str = "", user=Depends(auth)):
    if not uid:
        users = await hs.users()
        uid = next((u["id"] for u in users if u["name"] == HS_USER), users[0]["id"] if users else "1")
    return {"keys": await hs.keys(uid), "uid": uid}


@app.post("/tg/api/keys")
async def api_key_create(b=Depends(body), user=Depends(auth)):
    k = await hs.key_create(b.get("user") or "1", b.get("reusable", False), b.get("ephemeral", False),
                            int(b.get("hours") or 24), b.get("tags") or [])
    cmd = f"tailscale up --login-server {HS_URL} --authkey {k.get('key')}"
    return {"key": k, "cmd": cmd, "qr": qr_png_b64(cmd), "qr_key": qr_png_b64(k.get("key", ""))}


@app.post("/tg/api/keys/expire")
async def api_key_expire(b=Depends(body), user=Depends(auth)):
    await hs.key_expire(b.get("user") or "1", b.get("key", ""))
    return {"ok": True}


@app.post("/tg/api/qr")
async def api_qr(b=Depends(body), user=Depends(auth)):
    return {"qr": qr_png_b64(b.get("text", ""))}


@app.get("/tg/api/files")
async def api_files(user=Depends(auth)):
    return {"shares": [s.info() for s in shares.values()],
            "receives": sorted(receives.values(), key=lambda r: -r["started"])[:30]}


@app.post("/tg/api/files/share")
async def api_file_share(b=Depends(body), user=Depends(auth)):
    p = (OUT / b.get("name", "")).resolve()
    if OUT.resolve() not in p.parents or not p.exists():
        raise RuntimeError("no such file in out/")
    s = await share_path(p)
    return {"share": s.info(), "qr": qr_png_b64(s.ticket)}


@app.post("/tg/api/files/share/stop")
async def api_file_share_stop(b=Depends(body), user=Depends(auth)):
    s = shares.pop(b.get("name", ""), None)
    if s:
        await s.stop()
        if b.get("delete"):
            shutil.rmtree(s.path, ignore_errors=True) if s.path.is_dir() else s.path.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/tg/api/files/receive")
async def api_file_receive(b=Depends(body), user=Depends(auth)):
    m = re.search(r"(blob[a-z0-9]+)", (b.get("ticket") or "").strip())
    if not m:
        raise RuntimeError("not a sendme ticket")
    asyncio.create_task(receive_ticket(m.group(1), int(user.get("id")), lang_of(user)))
    return {"ok": True}


@app.post("/tg/api/files/receive/delete")
async def api_file_receive_delete(b=Depends(body), user=Depends(auth)):
    rid = b.get("id", "")
    receives.pop(rid, None)
    if re.fullmatch(r"[0-9a-f]{8}", rid):
        shutil.rmtree(IN / rid, ignore_errors=True)
    return {"ok": True}


@app.post("/tg/api/update/headscale")
async def api_update_hs(b=Depends(body), user=Depends(auth)):
    if not MANAGED:
        raise HTTPException(400, "headscale is not managed by HeadLauncher (attach mode)")
    asyncio.create_task(do_update_headscale(int(user["id"]), lang_of(user), b.get("version")))
    return {"ok": True}


@app.post("/tg/api/update/rollback")
async def api_rollback(user=Depends(auth)):
    if not MANAGED:
        raise HTTPException(400, "headscale is not managed by HeadLauncher (attach mode)")
    asyncio.create_task(do_rollback(int(user["id"]), lang_of(user)))
    return {"ok": True}


@app.post("/tg/api/update/bot")
async def api_update_bot(user=Depends(auth)):
    await asyncio.to_thread(updater.update_bot_detached)
    return {"ok": True}


@app.post("/tg/api/lang")
async def api_lang(b=Depends(body), user=Depends(auth)):
    ADM["lang"][str(user["id"])] = "ru" if b.get("lang") == "ru" else "en"
    save_admins(ADM)
    return {"ok": True}


# ----------------------------------------------------------------- update flows
UPD_LOCK = asyncio.Lock()


async def do_update_headscale(chat_id: int, lang: str, version: str | None):
    if UPD_LOCK.locked():
        return
    async with UPD_LOCK:
        new = version or await updater.latest_headscale()
        cur = updater.current_headscale()
        if not new or new == cur:
            await bot.send_message(chat_id, t(lang, "upd_none"))
            return
        await bot.send_message(chat_id, t(lang, "upd_started"))
        ok, logs = await updater.update_headscale(new, HS_API_URL + "/health")
        if ok:
            await bot.send_message(chat_id, t(lang, "upd_done", v=new), reply_markup=menu(lang))
        else:
            await bot.send_message(chat_id, t(lang, "upd_failed", err=logs[-800:], v=cur), reply_markup=menu(lang))


async def do_rollback(chat_id: int, lang: str):
    async with UPD_LOCK:
        await bot.send_message(chat_id, t(lang, "upd_started"))
        ok, logs = await updater.rollback_headscale(HS_API_URL + "/health")
        v = updater.current_headscale()
        await bot.send_message(chat_id, t(lang, "upd_done", v=v) if ok else t(lang, "upd_failed", err=logs[-800:], v=v),
                               reply_markup=menu(lang))


# ----------------------------------------------------------------- bot UI
def menu(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "b_panel"), web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text=t(lang, "b_status"), callback_data="status"),
         InlineKeyboardButton(text=t(lang, "b_nodes"), callback_data="nodes")],
        [InlineKeyboardButton(text=t(lang, "b_key"), callback_data="key"),
         InlineKeyboardButton(text=t(lang, "b_key_reuse"), callback_data="keyreuse")],
        [InlineKeyboardButton(text=t(lang, "b_update"), callback_data="update"),
         InlineKeyboardButton(text=t(lang, "b_help"), callback_data="help")],
    ])


def confirm(lang, go):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "b_yes"), callback_data=go),
        InlineKeyboardButton(text=t(lang, "b_no"), callback_data="menu")]])


async def update_view(lang):
    cur = updater.current_headscale()
    latest = await updater.latest_headscale()
    prev = updater.env_read().get("HEADSCALE_PREV")
    lines = [t(lang, "upd_title"), t(lang, "upd_hs", cur=cur, latest=latest or "?"), t(lang, "upd_bot", cur=updater.bot_version())]
    rows = []
    if not MANAGED:
        lines.append(t(lang, "upd_external"))
    elif latest and updater.newer(latest, cur):
        rows.append([InlineKeyboardButton(text=t(lang, "b_upd_hs", v=latest), callback_data="upd_hs_ask")])
    else:
        lines.append(t(lang, "upd_none"))
    if MANAGED and prev and prev != cur:
        rows.append([InlineKeyboardButton(text=t(lang, "b_rollback", v=prev), callback_data="upd_rb_ask")])
    rows.append([InlineKeyboardButton(text=t(lang, "b_upd_bot"), callback_data="upd_bot_ask")])
    rows.append([InlineKeyboardButton(text=t(lang, "b_back"), callback_data="menu")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def h_start(m: types.Message, command: CommandObject):
    lang = lang_of(m.from_user)
    code = (command.args or "").strip()
    if code and CLAIM_CODE and hmac.compare_digest(code, CLAIM_CODE):
        if is_admin(m.from_user.id):
            await m.answer(t(lang, "already_admin"), reply_markup=menu(lang))
            return
        if ADM["claimed"]:
            await m.answer(t(lang, "claim_used"))
            return
        ADM["ids"].append(m.from_user.id)
        ADM["claimed"] = True
        save_admins(ADM)
        await bot_setup()
        await m.answer(t(lang, "claimed"))
        await m.answer(t(lang, "welcome"))
        await send_key(m.chat.id, lang, reusable=False)
        return
    if not is_admin(m.from_user.id):
        await m.answer(t(lang, "not_admin"))
        return
    if not ADM.get("welcomed"):
        ADM["welcomed"] = True
        save_admins(ADM)
        await m.answer(t(lang, "welcome"))
        await send_key(m.chat.id, lang, reusable=False)
        return
    await m.answer(t(lang, "menu"), reply_markup=menu(lang))


def admin_msg(m: types.Message) -> bool:
    return bool(m.from_user) and is_admin(m.from_user.id)


@dp.message(Command("menu"), admin_msg)
async def h_menu(m: types.Message):
    lang = lang_of(m.from_user)
    await m.answer(t(lang, "menu"), reply_markup=menu(lang))


@dp.message(Command("status", "st"), admin_msg)
async def h_status(m: types.Message):
    lang = lang_of(m.from_user)
    await m.answer(await status_text(lang), reply_markup=menu(lang))


@dp.message(Command("nodes"), admin_msg)
async def h_nodes(m: types.Message):
    lang = lang_of(m.from_user)
    await m.answer(await nodes_text(lang), reply_markup=menu(lang))


@dp.message(Command("key"), admin_msg)
async def h_key(m: types.Message):
    reusable = any(a.lower() in ("reuse", "multi") for a in (m.text or "").split()[1:])
    await send_key(m.chat.id, lang_of(m.from_user), reusable)


@dp.message(Command("update"), admin_msg)
async def h_update(m: types.Message):
    txt, kb = await update_view(lang_of(m.from_user))
    await m.answer(txt, reply_markup=kb)


@dp.message(Command("help"), admin_msg)
async def h_help(m: types.Message):
    lang = lang_of(m.from_user)
    await m.answer(t(lang, "help"), reply_markup=menu(lang))


@dp.message(Command("lang"), admin_msg)
async def h_lang(m: types.Message):
    cur = lang_of(m.from_user)
    new = "en" if cur == "ru" else "ru"
    ADM["lang"][str(m.from_user.id)] = new
    save_admins(ADM)
    await m.answer(t(new, "menu"), reply_markup=menu(new))


@dp.message(F.document, admin_msg)
async def h_document(m: types.Message):
    lang = lang_of(m.from_user)
    name = re.sub(r"[^\w.\-()\[\] ]", "_", m.document.file_name or ("file_" + m.document.file_unique_id))
    dest = OUT / name
    note = await m.answer(t(lang, "downloading"))
    try:
        f = await bot.get_file(m.document.file_id)
        await bot.download_file(f.file_path, destination=str(dest))
        s = await share_path(dest)
        await note.delete()
        await m.answer_photo(types.BufferedInputFile(base64.b64decode(qr_png_b64(s.ticket)), "qr.png"),
                             caption=t(lang, "share_caption", name=s.name, size=human(s.size), ticket=s.ticket),
                             parse_mode="HTML")
    except Exception as e:
        await note.edit_text("❌ " + str(e))


@dp.message(F.text, admin_msg)
async def h_text(m: types.Message):
    lang = lang_of(m.from_user)
    txt = m.text.strip()
    mt = re.search(r"(blob[a-z0-9]{40,})", txt)
    if mt:
        await m.answer(t(lang, "recv_started"))
        asyncio.create_task(receive_ticket(mt.group(1), m.chat.id, lang))
        return
    if "/register/" in txt or "mkey:" in txt:
        try:
            r = await hs.register(HS_USER, txt)
            n = r.get("node") or {}
            await m.answer(t(lang, "registered", name=n.get("givenName") or n.get("name"), ips=", ".join(n.get("ipAddresses", []))),
                           reply_markup=menu(lang))
        except Exception as e:
            await m.answer("❌ " + str(e), reply_markup=menu(lang))
        return
    await m.answer(t(lang, "unknown"), reply_markup=menu(lang))


@dp.callback_query()
async def h_cb(cb: types.CallbackQuery):
    await cb.answer()
    if not cb.message or not is_admin(cb.from_user.id):
        return
    lang = lang_of(cb.from_user)
    d = cb.data or ""
    chat = cb.message.chat.id
    send = lambda txt, kb=None: bot.send_message(chat, txt, reply_markup=kb or menu(lang), disable_web_page_preview=True)
    try:
        if d == "status":
            await send(await status_text(lang))
        elif d == "nodes":
            await send(await nodes_text(lang))
        elif d in ("key", "keyreuse"):
            await send_key(chat, lang, d == "keyreuse")
        elif d == "help":
            await send(t(lang, "help"))
        elif d == "menu":
            await send(t(lang, "menu"))
        elif d == "update":
            txt, kb = await update_view(lang)
            await send(txt, kb)
        elif d in ("upd_hs_ask", "upd_hs_go", "upd_rb_ask", "upd_rb_go") and not MANAGED:
            await send(t(lang, "upd_external"))
        elif d == "upd_hs_ask":
            latest = await updater.latest_headscale()
            await send(t(lang, "upd_ask_hs", cur=updater.current_headscale(), new=latest), confirm(lang, "upd_hs_go"))
        elif d == "upd_hs_go":
            asyncio.create_task(do_update_headscale(chat, lang, None))
        elif d == "upd_rb_ask":
            await send(t(lang, "upd_ask_rb", v=updater.env_read().get("HEADSCALE_PREV")), confirm(lang, "upd_rb_go"))
        elif d == "upd_rb_go":
            asyncio.create_task(do_rollback(chat, lang))
        elif d == "upd_bot_ask":
            await send(t(lang, "upd_ask_bot"), confirm(lang, "upd_bot_go"))
        elif d == "upd_bot_go":
            await send(t(lang, "upd_bot_started"))
            await asyncio.to_thread(updater.update_bot_detached)
    except Exception as e:
        await send("❌ " + str(e))


async def bot_setup():
    await bot.set_my_commands([
        types.BotCommand(command="menu", description="menu"),
        types.BotCommand(command="status", description="status"),
        types.BotCommand(command="nodes", description="devices"),
        types.BotCommand(command="key", description="new key"),
        types.BotCommand(command="update", description="updates"),
        types.BotCommand(command="lang", description="ru/en"),
        types.BotCommand(command="help", description="help"),
    ])
    for a in ADM["ids"]:
        try:
            await bot.set_chat_menu_button(chat_id=a, menu_button=MenuButtonWebApp(text="Panel", web_app=WebAppInfo(url=WEBAPP_URL)))
        except Exception as e:
            log.warning("menu button for %s: %s", a, e)


# ----------------------------------------------------------------- main
async def main():
    await bot_setup()
    host, port = LISTEN.split(":")
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=int(port), log_level="warning"))
    poll = asyncio.create_task(dp.start_polling(bot, allowed_updates=["message", "callback_query"],
                                                handle_signals=False))
    for sig in (signal.SIGTERM, signal.SIGINT):  # uvicorn re-raises the captured signal after serve()
        signal.signal(sig, lambda *_: None)
    await server.serve()
    log.info("shutting down")
    for s in list(shares.values()):
        await s.stop()
    try:
        await dp.stop_polling()
    except Exception:
        pass
    poll.cancel()
    try:
        await asyncio.wait_for(poll, 5)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
