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


# 類別 → 中文標籤(autocomplete UI 顯示用)
_CATEGORY_LABELS = {
    "active": "主動式",
    "market": "市值型",
    "dividend": "高股息",
    "theme": "主題型",
    "overseas": "海外型",
    "leverage": "槓桿/反向",
    "bond": "債券型",
    "other": "其他",
}


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


@router.get("/etf/search")
async def search_etf(
    q: str = Query("", description="代號或名稱關鍵字"),
    limit: int = Query(12, ge=1, le=50),
    session: Session = Depends(get_session),
) -> dict:
    """ETF autocomplete 搜尋 — 給 compare 頁的 chip 選擇器。

    比對策略:
    - 代號開頭配對(0050 → 0050, 0050B 等)優先排序
    - 代號子字串配對次優
    - 中文名稱子字串再次
    - 排除 index 類別(TAIEX 大盤不該被當 ETF 選)
    """
    keyword = (q or "").strip().upper()
    if not keyword:
        # 空字串 → 回最熱門幾支(0050 / 0056 / 00878 等市值/高股息)
        rows = session.scalars(
            select(ETF)
            .where(ETF.is_active.is_(True))
            .where(ETF.category.in_(["market", "dividend", "active"]))
            .where(ETF.code.in_(["0050", "0056", "00878", "00919", "00929", "0056B", "00713"]))
            .limit(limit)
        ).all()
    else:
        like = f"%{keyword}%"
        prefix = f"{keyword}%"
        # 排除大盤指數
        rows = session.scalars(
            select(ETF)
            .where(ETF.is_active.is_(True))
            .where(ETF.category != "index")
            .where((ETF.code.ilike(like)) | (ETF.name.like(like)))
            .order_by(
                # 代號開頭配對的排最前
                ETF.code.ilike(prefix).desc(),
                ETF.code.asc(),
            )
            .limit(limit)
        ).all()

    return {
        "q": q,
        "items": [
            {
                "code": e.code,
                "name": e.name,
                "category": e.category,
                "category_label": _CATEGORY_LABELS.get(e.category, e.category),
            }
            for e in rows
        ],
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
