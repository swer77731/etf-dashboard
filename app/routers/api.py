"""JSON API routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
async def health(session: Session = Depends(get_session)) -> dict:
    """Liveness probe — also confirms DB is reachable."""
    db_ok = False
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # pragma: no cover
        db_ok = False

    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "db": "ok" if db_ok else "fail",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
