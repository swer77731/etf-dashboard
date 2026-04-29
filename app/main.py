"""FastAPI entry point — lifespan handles DB init + scheduler boot."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.config import PROJECT_ROOT, settings
from app.database import init_db
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Booting %s (env=%s)", settings.app_name, settings.app_env)
    init_db()
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

app.mount(
    "/static",
    CachedStaticFiles(directory=str(PROJECT_ROOT / "static")),
    name="static",
)

app.include_router(pages_router.router)
app.include_router(api_router.router)
app.include_router(monthly_income_router.router)
