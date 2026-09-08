"""Headscale v1 REST API client (headscale 0.26+)."""
import re
from datetime import datetime, timedelta, timezone

import httpx


class HS:
    def __init__(self, base: str, api_key: str):
        self.base = base.rstrip("/") + "/api/v1"
        self.c = httpx.AsyncClient(
            headers={"Authorization": "Bearer " + api_key},
            timeout=20,
        )

    async def _req(self, method, path, **kw):
        r = await self.c.request(method, self.base + path, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            raise RuntimeError(f"headscale {r.status_code}: {msg}")
        return r.json() if r.content else {}

    # --- nodes -----------------------------------------------------------
    async def nodes(self):
        return (await self._req("GET", "/node")).get("nodes", [])

    async def node(self, nid):
        return (await self._req("GET", f"/node/{nid}")).get("node", {})

    async def rename(self, nid, name):
        return await self._req("POST", f"/node/{nid}/rename/{name}")

    async def expire(self, nid):
        return await self._req("POST", f"/node/{nid}/expire")

    async def delete(self, nid):
        return await self._req("DELETE", f"/node/{nid}")

    async def tags(self, nid, tags):
        return await self._req("POST", f"/node/{nid}/tags", json={"tags": tags})

    async def approve_routes(self, nid, routes):
        return await self._req("POST", f"/node/{nid}/approve_routes", json={"routes": routes})

    async def register(self, user_name, key):
        """key: 'mkey:...' or the full /register/<key> URL from `tailscale up`."""
        m = re.search(r"(mkey:[0-9a-f]{64})", key)
        if not m:
            m = re.search(r"/register/([^\s/?#]+)", key)
        if not m:
            raise RuntimeError("не нашёл ключ регистрации (mkey:... или ссылка /register/...)")
        k = m.group(1)
        if not k.startswith("mkey:"):
            k = "mkey:" + k if re.fullmatch(r"[0-9a-f]{64}", k) else k
        return await self._req("POST", "/node/register", params={"user": user_name, "key": k})

    # --- users -----------------------------------------------------------
    async def users(self):
        return (await self._req("GET", "/user")).get("users", [])

    async def user_create(self, name):
        return await self._req("POST", "/user", json={"name": name})

    async def user_rename(self, uid, name):
        return await self._req("POST", f"/user/{uid}/rename/{name}")

    async def user_delete(self, uid):
        return await self._req("DELETE", f"/user/{uid}")

    # --- preauth keys ----------------------------------------------------
    async def keys(self, uid):
        return (await self._req("GET", "/preauthkey", params={"user": str(uid)})).get("preAuthKeys", [])

    async def key_create(self, uid, reusable=False, ephemeral=False, hours=24, tags=None):
        exp = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"user": str(uid), "reusable": bool(reusable), "ephemeral": bool(ephemeral),
                "expiration": exp, "aclTags": tags or []}
        return (await self._req("POST", "/preauthkey", json=body)).get("preAuthKey", {})

    async def key_expire(self, uid, key):
        return await self._req("POST", "/preauthkey/expire", json={"user": str(uid), "key": key})

    # --- misc ------------------------------------------------------------
    async def policy(self):
        return await self._req("GET", "/policy")

    async def health(self, public_url):
        try:
            r = await self.c.get(public_url.rstrip("/") + "/health", timeout=10)
            return "pass" in r.text
        except Exception:
            return False
