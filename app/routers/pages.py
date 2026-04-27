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
    codes: str = "0050,0056,00878",
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


_NEWS_DAYS_CHOICES = {"7": 7, "30": 30, "all": None}


@router.get("/news", response_class=HTMLResponse)
async def news(
    request: Request,
    etf: str | None = None,
    page: int = 1,
    days: str = "7",   # 7 / 30 / all,預設近 7 天(快訊)
) -> HTMLResponse:
    """新聞牆 — 100% 讀本地 news table。可選 ?etf=0050 過濾單一 ETF, ?days=7|30|all 切換期間。"""
    page = max(1, page)
    page_size = 30
    offset = (page - 1) * page_size

    days_int = _NEWS_DAYS_CHOICES.get(days, 7)
    items = news_sync.list_recent_news(
        etf_code=etf, limit=page_size, offset=offset, days=days_int,
    )
    total = news_sync.count_news(etf_code=etf, days=days_int)

    # 7 天 / 30 天 / 全部 三個 tab 各自的總數,UI 顯示用
    counts = {
        "7":   news_sync.count_news(etf_code=etf, days=7),
        "30":  news_sync.count_news(etf_code=etf, days=30),
        "all": news_sync.count_news(etf_code=etf, days=None),
    }

    return templates.TemplateResponse(
        request, "news.html",
        {
            **_common_ctx(),
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": offset + page_size < total,
            "etf_filter": etf.upper() if etf else None,
            "days_filter": days if days in _NEWS_DAYS_CHOICES else "7",
            "counts": counts,
            "today_iso": date.today().isoformat(),
        },
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


@router.get("/disclaimer", response_class=HTMLResponse)
async def disclaimer(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/disclaimer.html", _common_ctx())


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/terms.html", _common_ctx())


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/privacy.html", _common_ctx())
