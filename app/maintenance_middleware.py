"""MaintenanceMiddleware — 部署期間 / 手動維護模式回 503 + 維護頁。

行為
====
- app 未就緒(lifespan 還在跑)→ 503 + maintenance.html
- admin 手動 toggle ON → 同上
- 否則 → pass through

Whitelist(必須能通,否則 Zeabur 健檢失敗或 admin 救不回)
==========================================================
- /api/health   — Zeabur 健檢 endpoint
- /admin/       — admin 維護期間仍可進整個後台(切換 / 排查 / 改設定)
                  user 開維護模式是要「擋一般用戶、自己繼續做事」,
                  不是「把自己也鎖在外面」
- /favicon.ico  — 避免 console 噪音 / log spam

模板處理
========
模組 import 時讀取 templates/maintenance.html 一次,bytes 緩存在記憶體。
讀檔失敗(部署檔遺漏)→ 用內建 fallback 字串,絕不讓 middleware 自己崩。
"""
from __future__ import annotations

import logging
from pathlib import Path

from starlette.types import ASGIApp, Receive, Scope, Send

from app.maintenance import is_under_maintenance

logger = logging.getLogger(__name__)

_FALLBACK_HTML = (
    '<!doctype html><html lang="zh-Hant"><head>'
    '<meta charset="utf-8"><title>維護中</title>'
    '<meta http-equiv="refresh" content="30">'
    '<style>body{background:#0a0e1a;color:#e5e7eb;font-family:system-ui,sans-serif;'
    'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}'
    '.w{text-align:center}h1{font-size:1.75rem;margin-bottom:1rem}'
    'p{color:#6b7280}</style></head><body>'
    '<div class="w"><h1>網站更新維護中</h1><p>請稍後再試</p></div>'
    '</body></html>'
)


class MaintenanceMiddleware:
    """純 ASGI middleware。註冊順序要最後 add(LIFO → 第一個跑)。"""

    EXEMPT_PREFIXES = ("/api/health", "/admin/", "/favicon.ico")

    def __init__(self, app: ASGIApp, template_path: Path) -> None:
        self.app = app
        try:
            self._html_bytes = template_path.read_bytes()
            logger.info(
                "[maintenance-mw] loaded %s (%d bytes)",
                template_path.name, len(self._html_bytes),
            )
        except Exception:
            logger.exception(
                "[maintenance-mw] failed to read %s — using fallback HTML",
                template_path,
            )
            self._html_bytes = _FALLBACK_HTML.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "") or ""
        if any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        if not is_under_maintenance():
            await self.app(scope, receive, send)
            return

        # 503 + maintenance HTML
        body = self._html_bytes
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store, no-cache, must-revalidate"),
                (b"retry-after", b"30"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
