"""FastAPI entry point — lifespan handles DB init + scheduler boot."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.analytics_middleware import AnalyticsMiddleware
from app.config import PROJECT_ROOT, settings
from app.database import init_db
from app.routers import admin as admin_router
from app.routers import api as api_router
from app.routers import monthly_income as monthly_income_router
from app.routers import pages as pages_router
from app.scheduler import shutdown_scheduler, start_scheduler, startup_sync_if_needed


class CachedStaticFiles(StaticFiles):
    """StaticFiles + 1-year immutable Cache-Control。

    紀律 #16 — Tokyo→Taipei 每次 static 抓 130ms RTT 太傷,瀏覽器一輩子
    cache 該檔案。改 logo / chart-watermark.js 等資產時,**檔名加版本後綴**
    (logo.v2.svg)或在 query string 帶版本(?v=2)強制 reload,**不然
    1 年內舊客戶看到的是舊檔**。風險可接受 — 我們的 static 變動本來就少。
    """
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class ServerTimingMiddleware:
    """每個 response 加 Server-Timing: total;dur=X.X header。

    紀律 #16 — DevTools Network 直接看到 server 處理時間,
    user 反映「某頁慢」時可立即分辨是 server 還是網路。
    純 ASGI middleware 比 BaseHTTPMiddleware 輕,不額外多一層 async wrap。
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()

        async def send_with_timing(message: Message):
            if message["type"] == "http.response.start":
                elapsed_ms = (time.perf_counter() - start) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"server-timing", f"total;dur={elapsed_ms:.1f}".encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_timing)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def _reset_finmind_quota_on_boot() -> None:
    """容器啟動時清空 finmind_quota_log(只在 production env)。

    紀律 #16 — Bug 4:容器重啟後,前次運行寫進 quota log 的 timestamps 仍
    在 60 分鐘窗口內被 count_recent_calls 看見 → 開機立刻判 quota 用爆 →
    news_15min cron 觸發後 finmind.request 走 quota gate sleep 60 分鐘 →
    一輪輪 sleep,新聞永遠不抓。清掉讓計數從 boot 重算。

    不影響 universe/holdings/dividend cron 邏輯 — 它們的 quota gate 仍照常
    跑、record 仍照常寫,只是從 0 重新累積。
    """
    if settings.app_env != "production":
        return
    try:
        from sqlalchemy import text
        from app.database import session_scope
        with session_scope() as s:
            r = s.execute(text("DELETE FROM finmind_quota_log"))
            logger.info(
                "[startup] cleared %d rows from finmind_quota_log (production boot — Bug 4 fix)",
                r.rowcount,
            )
    except Exception:
        logger.exception("[startup] reset finmind_quota_log failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Booting %s (env=%s)", settings.app_name, settings.app_env)
    init_db()
    _reset_finmind_quota_on_boot()
    start_scheduler()
    startup_sync_if_needed()  # 背景跑,不卡 web 啟動
    logger.info("Startup complete — listening on %s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        logger.info("Shutting down…")
        shutdown_scheduler()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

# 紀律 #16 — 70KB 純文字 HTML 沒壓縮 = 多浪費 60KB / 使用者。
# minimum_size=500:小 JSON / 短 HTML 不壓(壓比反而升、CPU 多餘)。
# CachedStaticFiles 已掛 immutable header,gzip 中間層也會壓 SVG / JS / CSS,double win。
app.add_middleware(GZipMiddleware, minimum_size=500)

# Server-Timing header — DevTools Network 直接看 server time,診斷「慢」議題用。
# 註冊順序:後加先跑(LIFO),所以實際 request 先過 ServerTiming 計時 → 再 gzip 壓縮。
app.add_middleware(ServerTimingMiddleware)

# 客戶紀錄分析 — 攔每個 GET 寫 analytics_log + compare_log。
# 排除清單:/static、/admin/*、/api/etf/search、/healthz、/favicon.ico。
# Session cookie 7 天、IP 末段遮、5 秒去重。
app.add_middleware(AnalyticsMiddleware)

app.mount(
    "/static",
    CachedStaticFiles(directory=str(PROJECT_ROOT / "static")),
    name="static",
)

app.include_router(pages_router.router)
app.include_router(api_router.router)
app.include_router(monthly_income_router.router)
app.include_router(admin_router.router)
