"""JSON API routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.models.etf import ETF
from app.models.kbar import DailyKBar
from app.services import finmind, ranking

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

    etf_count = session.scalar(select(func.count(ETF.id))) or 0
    kbar_count = session.scalar(select(func.count(DailyKBar.id))) or 0

    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "db": "ok" if db_ok else "fail",
        "etf_count": etf_count,
        "kbar_count": kbar_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/quota")
async def quota() -> dict:
    """FinMind 配額即時狀況 — 監控 50% 紅線。"""
    q = finmind.check_quota(force=True)
    return {
        "level": q.level,
        "used": q.used,
        "limit_hour": q.limit_hour,
        "ratio_pct": round(q.ratio * 100, 2),
        "room_left_for_us": q.room,
        "near_red_line": q.near_red_line,
        "policy": "我們最多用 50% 配額,剩下留給其他共用者",
    }


@router.get("/ranking")
async def get_ranking(
    category: str = Query("market", description="market / dividend / active / theme / overseas / leverage / bond / other"),
    period: str = Query("3m", description="1m / 3m / ytd / 1y / 3y"),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    """JSON 版排行榜 — 給未來行動 APP / 第三方串接用。"""
    if period not in ranking.PERIOD_LABELS:
        raise HTTPException(400, f"invalid period: {period}")
    result = ranking.get_ranking(category, period, limit=limit)
    # dataclass 不能直接 JSON serialize → 轉 dict
    result["rows"] = [r.__dict__ for r in result["rows"]]
    return result
