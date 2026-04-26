"""FastAPI entry point — lifespan handles DB init + scheduler boot."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, settings
from app.database import init_db
from app.routers import api as api_router
from app.routers import pages as pages_router
from app.scheduler import shutdown_scheduler, start_scheduler

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
    StaticFiles(directory=str(PROJECT_ROOT / "static")),
    name="static",
)

app.include_router(pages_router.router)
app.include_router(api_router.router)
