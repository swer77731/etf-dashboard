"""RefVisitorMiddleware — 處理訪客 ?ref=XXX。

- 攔每個 GET HTML 請求,檢查 query 有 `ref=XXX`(6 字元 [A-Z0-9])
- 透過 share_service.process_ref_visit 寫一筆 share_clicks(已 24h dedupe)
- 把 share_clicks.id 寫進 cookie evw_ref_click(30 天)給 30s ping 用
- skip /api / /admin / /static / /auth(紀律 #14:不做沒必要的事)
- 失敗 silent — 不擋 user request(紀律 #20)
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

_REF_CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
_SKIP_PREFIXES = ("/api/", "/admin/", "/static/", "/auth/")
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days


class RefVisitorMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "") or ""
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        qs = (scope.get("query_string") or b"").decode("latin-1", errors="ignore")
        if "ref=" not in qs:
            await self.app(scope, receive, send)
            return

        params = parse_qs(qs)
        ref_values = params.get("ref") or []
        ref_code = (ref_values[0] if ref_values else "").upper()
        if not _REF_CODE_RE.match(ref_code):
            await self.app(scope, receive, send)
            return

        ip = self._extract_ip(scope)
        ua = self._extract_ua(scope)

        click_id: int | None = None
        try:
            from app.services.share_service import process_ref_visit
            click_id = process_ref_visit(ref_code, ip, ua)
        except Exception:
            logger.exception("[ref-mw] process_ref_visit raised")

        if click_id is None:
            await self.app(scope, receive, send)
            return

        cookie_value = (
            f"evw_ref_click={click_id}; Path=/; Max-Age={_COOKIE_MAX_AGE}; "
            f"SameSite=Lax; HttpOnly"
        ).encode("latin-1")

        async def send_with_cookie(message: Message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", cookie_value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    @staticmethod
    def _extract_ip(scope: Scope) -> str | None:
        headers = dict(scope.get("headers") or [])
        xff = headers.get(b"x-forwarded-for")
        if xff:
            return xff.decode("latin-1").split(",")[0].strip()
        cf = headers.get(b"cf-connecting-ip")
        if cf:
            return cf.decode("latin-1").strip()
        client = scope.get("client")
        if client and len(client) >= 1:
            return client[0]
        return None

    @staticmethod
    def _extract_ua(scope: Scope) -> str | None:
        headers = dict(scope.get("headers") or [])
        ua = headers.get(b"user-agent")
        return ua.decode("latin-1") if ua else None
