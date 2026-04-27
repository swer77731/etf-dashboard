"""HTML page routes — server-rendered Jinja2 + HTMX."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.config import PROJECT_ROOT, settings
from app.services import dividend_metrics, etf_metrics, news_sync, performance, ranking

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


_LOGO_SVG = PROJECT_ROOT / "static" / "img" / "logo.svg"
_LOGO_PNG = PROJECT_ROOT / "static" / "img" / "logo.png"
_FAVICON  = PROJECT_ROOT / "static" / "img" / "favicon.ico"


def _detect_brand_assets() -> dict:
    """偵測 LOGO / favicon 是否存在,讓 template 自動切換。

    user 把檔案丟進 static/img/ 後,所有頁面自動使用 LOGO,不必改程式碼。
    """
    if _LOGO_SVG.exists():
        logo_url = "/static/img/logo.svg"
    elif _LOGO_PNG.exists():
        logo_url = "/static/img/logo.png"
    else:
        logo_url = None
    return {
        "logo_url": logo_url,
        "favicon_url": "/static/img/favicon.ico" if _FAVICON.exists() else None,
        "has_logo": logo_url is not None,
    }


def _common_ctx() -> dict:
    """共用品牌 context — 所有頁面都會用到。"""
    return {
        "app_name": settings.app_name,
        "app_env": settings.app_env,
        "brand_zh": settings.app_name,
        "brand_en": settings.app_brand_en,
        "brand_full": settings.app_brand_full,
        **_detect_brand_assets(),
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """首頁 — 大盤概況 + 6 個排行榜 sections。"""
    try:
        market = ranking.get_market_overview()
    except Exception:
        logger.exception("[index] market_overview failed")
        market = None

    sections = []

    try:
        sections.append({
            "kind": "top_movers",
            "category": "top",
            "title": "近月最火 ETF",
            "subtitle": "不分類別,看誰最近表現最強",
            "data": ranking.get_top_movers("1m", limit=10),
        })
    except Exception:
        logger.exception("[index] top_movers failed")
        sections.append({"kind": "top_movers", "category": "top", "title": "近月最火 ETF", "data": None})

    for cat_code, cat_label in [("active", "主動式"), ("market", "市值型"), ("dividend", "高股息")]:
        try:
            sections.append({
                "kind": "category",
                "category": cat_code,
                "title": f"{cat_label}(近 3 個月)",
                "data": ranking.get_ranking(cat_code, "3m", limit=10),
            })
        except Exception:
            logger.exception("[index] category %s failed", cat_code)
            sections.append({"kind": "category", "category": cat_code, "title": cat_label, "data": None})

    for direction, kind, title in [
        ("positive", "leverage_pos", "槓桿型 ETF(高風險)— 近 3 個月"),
        ("inverse",  "leverage_neg", "反向型 ETF(高風險)— 近 3 個月"),
    ]:
        try:
            sections.append({
                "kind": kind,
                "category": kind,
                "title": title,
                "data": ranking.get_leverage_ranking("3m", direction, limit=10),
            })
        except Exception:
            logger.exception("[index] leverage %s failed", direction)
            sections.append({"kind": kind, "category": kind, "title": title, "data": None})

    # Phase 1B 配息公布欄 — 未來 14 天「即將除息」
    # (FinMind TaiwanStockDividend 不回未來,需 Phase 1B-2 TWSE 爬蟲補,
    #  目前資料只到「已除息」,UI 顯示空狀態 + 友善文字)
    try:
        upcoming = dividend_metrics.get_upcoming_dividends(days=14, past_days=0)
    except Exception:
        logger.exception("[index] upcoming_dividends failed")
        upcoming = None

    return templates.TemplateResponse(
        request, "index.html",
        {
            **_common_ctx(),
            "sections": sections,
            "market": market,
            "upcoming_dividends": upcoming,
            "upcoming_group_labels": dividend_metrics.GROUP_LABELS,
        },
    )


def _parse_date(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return default


@router.get("/compare", response_class=HTMLResponse)
async def compare(
    request: Request,
    codes: str = "",
    start: str | None = None,
    end: str | None = None,
) -> HTMLResponse:
    """績效比較頁 — 自選 ETF + 自選日期區間 + 統計表 + 累積報酬走勢圖。"""
    today = date.today()
    end_date = _parse_date(end, today)
    start_date = _parse_date(start, today - timedelta(days=365))
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    # 上限 6 支(避免圖表太擠)
    code_list = [c.strip().upper() for c in codes.split(",") if c.strip()][:6]

    from dataclasses import asdict
    result = performance.compare_etfs(code_list, start_date, end_date)
    # 把 dataclass(slots=True 沒 __dict__)轉成 plain dict 給 template 與 ECharts JSON 用
    stats_dicts = [asdict(s) for s in result["stats"]]

    # 合成 chip 顯示用的 codes_list — found 用完整資訊,not_found/insufficient 仍保留代號讓 user 看見
    stats_by_code = {s["code"]: s for s in stats_dicts}
    codes_list = []
    for c in code_list:
        if c in stats_by_code:
            s = stats_by_code[c]
            codes_list.append({
                "code": c,
                "name": s["name"],
                "category": s["category"],
                "category_label": s["category_label"],
            })
        else:
            codes_list.append({"code": c, "name": "", "category": "", "category_label": ""})

    return templates.TemplateResponse(
        request, "compare.html",
        {
            **_common_ctx(),
            "result": {
                **result,
                "stats": stats_dicts,
            },
            "form": {
                "codes": ",".join(code_list),
                "codes_list": codes_list,
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
        },
    )


# 「全部」改 365 天(原本 None = 全表),124 NULL row 自動排除
_NEWS_DAYS_CHOICES = {"7": 7, "30": 30, "all": 365}

# 前端 filter + pagination 上限,避免極端情境(例如 365 天有上萬筆)炸瀏覽器
_NEWS_HARD_LIMIT = 5000


@router.get("/news", response_class=HTMLResponse)
async def news(
    request: Request,
    etf: str | None = None,
    days: str = "7",   # 7 / 30 / all,預設近 7 天(快訊)
) -> HTMLResponse:
    """新聞牆 — 100% 讀本地 news table。

    路由不分頁:一次回該窗口全部 row,前端用 JS slice 模擬分頁 + 即時搜尋(操作整個 array)。
    最多 5000 筆 hard cap 避免極端情境炸瀏覽器。
    """
    days_int = _NEWS_DAYS_CHOICES.get(days, 7)
    items = news_sync.list_recent_news(
        etf_code=etf, limit=_NEWS_HARD_LIMIT, offset=0, days=days_int,
    )

    # 7 天 / 30 天 / 全部 三個 tab 各自的總數,UI 顯示用
    counts = {
        "7":   news_sync.count_news(etf_code=etf, days=7),
        "30":  news_sync.count_news(etf_code=etf, days=30),
        "all": news_sync.count_news(etf_code=etf, days=_NEWS_DAYS_CHOICES["all"]),
    }

    return templates.TemplateResponse(
        request, "news.html",
        {
            **_common_ctx(),
            "items": items,
            "total": len(items),
            "etf_filter": etf.upper() if etf else None,
            "days_filter": days if days in _NEWS_DAYS_CHOICES else "7",
            "counts": counts,
            "today_iso": date.today().isoformat(),
        },
    )


@router.get("/holdings", response_class=HTMLResponse)
async def holdings_page(request: Request, codes: str = "") -> HTMLResponse:
    """ETF 持股分析頁 — Alpine.js + AJAX,前端打 /api/etf/{code}/holdings。

    URL ?codes=0050,0056 → 預先勾選那些 ETF。
    上限 3 支(plan 鎖定)。
    """
    initial_codes: list[dict] = []
    code_list = [c.strip().upper() for c in codes.split(",") if c.strip()][:3]
    if code_list:
        from app.models.etf import ETF
        from app.database import session_scope
        from sqlalchemy import select
        with session_scope() as s:
            etfs = s.scalars(select(ETF).where(ETF.code.in_(code_list))).all()
            etf_map = {e.code: e for e in etfs}
        for c in code_list:
            e = etf_map.get(c)
            initial_codes.append({
                "code": c,
                "name": e.name if e else "",
                "category": e.category if e else "",
            })

    return templates.TemplateResponse(
        request, "holdings.html",
        {**_common_ctx(), "initial_codes": initial_codes},
    )


@router.get("/etf/{code}", response_class=HTMLResponse)
async def etf_detail(request: Request, code: str) -> HTMLResponse:
    """ETF 詳情頁 — 100% 讀本地 DB,不打外部 API。"""
    detail = etf_metrics.get_etf_detail(code)
    if not detail:
        raise HTTPException(status_code=404, detail=f"找不到 ETF: {code}")
    related_news = news_sync.list_recent_news(etf_code=code.upper(), limit=10)
    return templates.TemplateResponse(
        request, "etf_detail.html",
        {**_common_ctx(), "etf": detail, "related_news": related_news},
    )


@router.get("/test_holdings", response_class=HTMLResponse)
async def test_holdings(request: Request, code: str = "0050") -> HTMLResponse:
    """Debug 頁 — 驗證 holdings + holdings_change 資料。

    用法:/test_holdings?code=0050
    """
    from app.models.etf import ETF
    from app.models.holdings import Holding
    from app.models.holdings_change import HoldingsChange
    from app.services.sync_status import get_sync_status
    from app.database import session_scope
    from sqlalchemy import select, func, desc

    code = code.upper()
    holdings_data: dict = {}
    changes_data: dict = {}

    with session_scope() as s:
        etf = s.scalar(select(ETF).where(ETF.code == code))
        if etf:
            # 最新 batch holdings
            latest_h = s.scalar(
                select(func.max(Holding.updated_at)).where(Holding.etf_id == etf.id)
            )
            if latest_h:
                rows = s.scalars(
                    select(Holding).where(Holding.etf_id == etf.id)
                    .where(Holding.updated_at == latest_h)
                    .order_by(Holding.rank.asc())
                ).all()
                holdings_data = {
                    "updated_at": latest_h.isoformat(),
                    "rows": [{
                        "rank": r.rank, "stock_code": r.stock_code,
                        "stock_name": r.stock_name, "weight": r.weight,
                        "sector": r.sector,
                    } for r in rows],
                }
            # 最新 batch changes
            latest_c = s.scalar(
                select(func.max(HoldingsChange.updated_at)).where(HoldingsChange.etf_id == etf.id)
            )
            if latest_c:
                cs = s.scalars(
                    select(HoldingsChange).where(HoldingsChange.etf_id == etf.id)
                    .where(HoldingsChange.updated_at == latest_c)
                    .order_by(desc(HoldingsChange.shares_diff))
                ).all()
                changes_data = {
                    "updated_at": latest_c.isoformat(),
                    "latest_date": cs[0].latest_date.isoformat() if cs else None,
                    "previous_date": cs[0].previous_date.isoformat() if cs else None,
                    "buy": [{"stock_code": r.stock_code, "stock_name": r.stock_name,
                             "shares_diff": r.shares_diff, "weight_latest": r.weight_latest}
                            for r in cs if r.change_direction == "buy"],
                    "sell": [{"stock_code": r.stock_code, "stock_name": r.stock_name,
                              "shares_diff": r.shares_diff, "weight_latest": r.weight_latest}
                             for r in cs if r.change_direction == "sell"],
                    "new": [{"stock_code": r.stock_code, "stock_name": r.stock_name,
                             "shares_diff": r.shares_diff, "weight_latest": r.weight_latest}
                            for r in cs if r.change_direction == "new"],
                }

    sync_st = get_sync_status("holdings_cmoney")
    return templates.TemplateResponse(
        request, "test_holdings.html",
        {
            **_common_ctx(),
            "code": code,
            "etf_found": etf is not None,
            "etf_name": etf.name if etf else None,
            "holdings": holdings_data,
            "changes": changes_data,
            "sync_status": sync_st,
        },
    )


@router.get("/disclaimer", response_class=HTMLResponse)
async def disclaimer(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/disclaimer.html", _common_ctx())


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/terms.html", _common_ctx())


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/privacy.html", _common_ctx())
