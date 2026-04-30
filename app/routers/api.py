"""JSON API routes."""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session, session_scope
from app.models.etf import ETF
from app.models.holdings import Holding
from app.models.holdings_change import HoldingsChange
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


# 紀律 #16 — autocomplete 高頻打,255 ETF 全表載進記憶體 5 分鐘 refresh
# 完全不打 DB,server time 從 60ms → <1ms。daily 14:30 universe sync 會新增
# 新 ETF,5 分鐘 TTL 對使用者體感無感。
_ETF_LIST_CACHE: list[dict] = []
_ETF_LIST_EXPIRES: float = 0.0
_ETF_LIST_TTL = 300.0


def _get_etf_list() -> list[dict]:
    """全 ETF list memoized,5 分鐘 refresh 一次。"""
    global _ETF_LIST_EXPIRES
    now = _time.monotonic()
    if _ETF_LIST_CACHE and now < _ETF_LIST_EXPIRES:
        return _ETF_LIST_CACHE
    with session_scope() as s:
        rows = s.scalars(
            select(ETF)
            .where(ETF.is_active.is_(True))
            .where(ETF.category != "index")
            .order_by(ETF.code.asc())
        ).all()
        snapshot = [
            {
                "code": e.code,
                "code_upper": e.code.upper(),
                "name": e.name or "",
                "category": e.category,
                "category_label": _CATEGORY_LABELS.get(e.category, e.category),
            }
            for e in rows
        ]
    _ETF_LIST_CACHE.clear()
    _ETF_LIST_CACHE.extend(snapshot)
    _ETF_LIST_EXPIRES = now + _ETF_LIST_TTL
    return _ETF_LIST_CACHE


_DEFAULT_EMPTY_CODES = ("0050", "0056", "00878", "00919", "00929", "0056B", "00713")


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


# CMoney 揭露 ETF Top 10 持股,完整 batch 應該 10 筆。Sync 偶爾被截
# 半路(收盤前未公告完整、CMoney rate-limit、timeout)→ 寫進 5 筆就 commit。
# API 應該避開不完整 batch,讓前端永遠看到完整圖景。
HOLDINGS_TARGET_ROWS = 10


@router.get("/etf/{code}/holdings")
async def get_etf_holdings(
    code: str,
    session: Session = Depends(get_session),
) -> dict:
    """ETF 持股 — 取「最新有 ≥ 10 筆」的 batch(自動跳過不完整 batch)。

    100% 讀本地 holdings table(資料主權鐵律)。
    若連一個完整 batch 都沒有,降級回傳最新批次 + is_partial=True。
    """
    code = code.upper()
    etf = session.scalar(select(ETF).where(ETF.code == code))
    if not etf:
        raise HTTPException(404, f"ETF not found: {code}")

    # Step 1:找「最新有 ≥ 10 筆」的 batch
    complete_batch = session.scalar(
        select(Holding.updated_at)
        .where(Holding.etf_id == etf.id)
        .group_by(Holding.updated_at)
        .having(func.count(Holding.id) >= HOLDINGS_TARGET_ROWS)
        .order_by(Holding.updated_at.desc())
        .limit(1)
    )

    # Step 2:沒完整 batch → fallback 最新 batch(避免空畫面)
    chosen_batch = complete_batch or session.scalar(
        select(func.max(Holding.updated_at)).where(Holding.etf_id == etf.id)
    )
    if chosen_batch is None:
        return {
            "code": code, "name": etf.name,
            "updated_at": None, "holdings": [], "is_partial": False,
        }

    # Step 3:拿選中 batch 的 rows
    rows = session.scalars(
        select(Holding)
        .where(Holding.etf_id == etf.id)
        .where(Holding.updated_at == chosen_batch)
        .order_by(Holding.rank.asc())
    ).all()
    return {
        "code": code,
        "name": etf.name,
        "updated_at": chosen_batch.isoformat(),
        "is_partial": len(rows) < HOLDINGS_TARGET_ROWS,
        "holdings": [{
            "rank": r.rank,
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "weight": r.weight,
            "sector": r.sector,
        } for r in rows],
    }


_ALLOWED_DAYS = (1, 7, 30)


def _compute_holdings_change_window(
    session: Session, etf_id: int, days: int,
) -> dict:
    """從 holdings 表 daily snapshots 算 latest 與 N 批前的權重 diff。

    紀律 #16 — 直接讀 holdings.weight,沒有 shares 資料故用權重 diff(%)
    取代 shares_diff(更貼合 ETF 曝險語意,客戶看「重押 +0.45%」更直觀)。

    days ∈ {1, 7, 30} — 找 distinct updated_at DESC 取 [0] 與 [days],
    若資料不夠就用最舊那批。
    """
    from sqlalchemy import desc, distinct

    dates = session.scalars(
        select(distinct(Holding.updated_at))
        .where(Holding.etf_id == etf_id)
        .order_by(desc(Holding.updated_at))
    ).all()

    if not dates:
        return {"latest_date": None, "previous_date": None,
                "buy": [], "sell": [], "new": []}

    latest_dt = dates[0]
    prev_idx = min(days, len(dates) - 1)
    prev_dt = dates[prev_idx]

    if latest_dt == prev_dt:
        return {
            "latest_date": latest_dt.date().isoformat(),
            "previous_date": prev_dt.date().isoformat(),
            "buy": [], "sell": [], "new": [],
        }

    latest_rows = session.scalars(
        select(Holding).where(Holding.etf_id == etf_id, Holding.updated_at == latest_dt)
    ).all()
    prev_rows = session.scalars(
        select(Holding).where(Holding.etf_id == etf_id, Holding.updated_at == prev_dt)
    ).all()

    latest_map = {h.stock_code: h for h in latest_rows}
    prev_map = {h.stock_code: h for h in prev_rows}

    buy: list[dict] = []
    sell: list[dict] = []
    new_: list[dict] = []
    NOISE_THRESHOLD = 0.01   # 權重 diff 小於 0.01% 視為雜訊不顯示

    for code, h in latest_map.items():
        prev_h = prev_map.get(code)
        if prev_h is None:
            new_.append({
                "stock_code": code,
                "stock_name": h.stock_name,
                "direction": "new",
                "weight_diff": round(h.weight, 3),
                "weight_latest": round(h.weight, 3),
            })
        else:
            wdiff = h.weight - prev_h.weight
            if abs(wdiff) < NOISE_THRESHOLD:
                continue
            entry = {
                "stock_code": code,
                "stock_name": h.stock_name,
                "direction": "buy" if wdiff > 0 else "sell",
                "weight_diff": round(wdiff, 3),
                "weight_latest": round(h.weight, 3),
            }
            (buy if wdiff > 0 else sell).append(entry)

    # 從 Top 10 掉出去 = 該批未列在前 10 → 視為「賣出」(weight 降到 0)
    for code, prev_h in prev_map.items():
        if code in latest_map:
            continue
        sell.append({
            "stock_code": code,
            "stock_name": prev_h.stock_name,
            "direction": "sell",
            "weight_diff": round(-prev_h.weight, 3),
            "weight_latest": 0.0,
        })

    buy.sort(key=lambda x: x["weight_diff"], reverse=True)
    sell.sort(key=lambda x: x["weight_diff"])   # 最負的先(賣超最多)
    new_.sort(key=lambda x: x["weight_diff"], reverse=True)

    return {
        "latest_date": latest_dt.date().isoformat(),
        "previous_date": prev_dt.date().isoformat(),
        "buy": buy[:10],
        "sell": sell[:10],
        "new": new_[:10],
    }


@router.get("/etf/{code}/holdings_change")
async def get_etf_holdings_change(
    code: str,
    days: int = Query(7, description="回看交易日數,允許 1 / 7 / 30"),
    session: Session = Depends(get_session),
) -> dict:
    """ETF 近 N 個交易日持股變動 — 買超 / 賣超 / 新增。

    100% 讀本地 holdings table 算權重 diff。
    days 不在 {1,7,30} → 回退 7。
    """
    if days not in _ALLOWED_DAYS:
        days = 7

    code = code.upper()
    etf = session.scalar(select(ETF).where(ETF.code == code))
    if not etf:
        raise HTTPException(404, f"ETF not found: {code}")

    payload = _compute_holdings_change_window(session, etf.id, days)
    return {
        "code": code,
        "name": etf.name,
        "days": days,
        **payload,
    }


# 舊路由保留(備用 — 用既有 holdings_change 表,有 shares_diff)
@router.get("/etf/{code}/holdings_change_legacy")
async def get_etf_holdings_change_legacy(
    code: str,
    session: Session = Depends(get_session),
) -> dict:
    """舊版 API — 讀 holdings_change pre-computed 表(CMoney 10-day window)。

    保留供未來除錯比對 / API 兼容,UI 不再用。
    """
    code = code.upper()
    etf = session.scalar(select(ETF).where(ETF.code == code))
    if not etf:
        raise HTTPException(404, f"ETF not found: {code}")

    latest_batch = session.scalar(
        select(func.max(HoldingsChange.updated_at)).where(HoldingsChange.etf_id == etf.id)
    )
    if latest_batch is None:
        return {
            "code": code, "name": etf.name, "updated_at": None,
            "latest_date": None, "previous_date": None,
            "buy": [], "sell": [], "new": [],
        }

    rows = session.scalars(
        select(HoldingsChange)
        .where(HoldingsChange.etf_id == etf.id)
        .where(HoldingsChange.updated_at == latest_batch)
    ).all()

    buy = sorted([r for r in rows if r.change_direction == "buy"],
                 key=lambda r: r.shares_diff, reverse=True)[:10]
    sell = sorted([r for r in rows if r.change_direction == "sell"],
                  key=lambda r: r.shares_diff)[:10]
    new = sorted([r for r in rows if r.change_direction == "new"],
                 key=lambda r: r.shares_diff, reverse=True)[:10]

    def _to_dict(r):
        return {
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "direction": r.change_direction,
            "shares_diff": r.shares_diff,
            "weight_latest": r.weight_latest,
        }

    latest_date = rows[0].latest_date.isoformat() if rows else None
    previous_date = rows[0].previous_date.isoformat() if rows else None

    return {
        "code": code,
        "name": etf.name,
        "updated_at": latest_batch.isoformat(),
        "latest_date": latest_date,
        "previous_date": previous_date,
        "buy": [_to_dict(r) for r in buy],
        "sell": [_to_dict(r) for r in sell],
        "new": [_to_dict(r) for r in new],
    }


def _log_search(q: str, hits: int, ua: str | None = None) -> None:
    """搜尋紀律 #16 — 寫進 search_log table。Bot UA 不寫。"""
    if not q:
        return
    # Bot 黑名單(同 analytics_middleware,避免 search_log 被 scanner 灌水)
    from app.analytics_middleware import _is_bot_ua
    if _is_bot_ua(ua):
        return
    try:
        from datetime import datetime, timezone
        from app.database import session_scope
        from app.models.analytics import SearchLog
        with session_scope() as s:
            s.add(SearchLog(
                q=q[:128],
                hits=hits,
                ts=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            ))
    except Exception:
        # log 寫失敗不能擋使用者搜尋
        import logging
        logging.getLogger(__name__).warning("[search_log] write failed", exc_info=True)


@router.get("/etf/search")
async def search_etf(
    request: Request,
    q: str = Query("", description="代號或名稱關鍵字"),
    limit: int = Query(20, ge=1, le=80),
    code_only: bool = Query(False, description="True 時只比對代號,不做名稱模糊搜尋"),
) -> dict:
    """ETF autocomplete 搜尋 — sidebar 全站搜尋 + compare 頁 chip 選擇器共用。

    純記憶體 list filter(_get_etf_list 5 分鐘 refresh)— 不打 DB,
    server time < 1ms。

    排序優先級:
    1. 代號完全相等(打 0050 → 0050 第一)
    2. 代號開頭配對(0050 → 0050B、00500;00981 → 009810~9 + 00981A/T)
    3. (預設)名稱子字串配對 — 但 code_only=1 時略過

    code_only=1 用途:compare 頁(user 要求純代號數字關聯,不要名稱模糊搜尋)。
    code_only=0 預設:sidebar 全站搜尋(可打「高股息」之類找 ETF)。
    排除 index 類別(TAIEX 大盤不該被當 ETF 選)。
    """
    etfs = _get_etf_list()
    keyword = (q or "").strip().upper()

    if not keyword:
        # 空字串 → 回固定熱門幾支(同舊 SQL 邏輯),不寫 search_log
        chosen = [e for e in etfs if e["code"] in _DEFAULT_EMPTY_CODES][:limit]
        return {
            "q": q,
            "items": [{"code": e["code"], "name": e["name"],
                       "category": e["category"], "category_label": e["category_label"]}
                      for e in chosen],
        }

    # 不分大小寫 substring 比對(SQL 用 ilike 可不分大小寫)
    matched = []
    for e in etfs:
        code_u = e["code_upper"]
        if keyword in code_u:
            matched.append(e)
        elif (not code_only) and (keyword in e["name"]):
            # name 不轉 upper 因為中文 .upper() 是 noop;舊 SQL 用 like(case-sensitive)
            matched.append(e)

    # 三層排序(stable sort):
    # 1. 完全相等最前
    # 2. prefix 配對次之
    # 3. 字母順
    def _sort_key(e):
        code_u = e["code_upper"]
        return (0 if code_u == keyword else 1,
                0 if code_u.startswith(keyword) else 1,
                code_u)
    matched.sort(key=_sort_key)
    chosen = matched[:limit]

    # 紀律 #16 — search_log: 紀錄真實搜尋(非空 q)+ 命中筆數,bot 過濾
    _log_search(q.strip(), len(chosen), ua=request.headers.get("user-agent"))

    return {
        "q": q,
        "items": [{"code": e["code"], "name": e["name"],
                   "category": e["category"], "category_label": e["category_label"]}
                  for e in chosen],
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
