"""HTML page routes — server-rendered Jinja2 + HTMX."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from fastapi import HTTPException

from app.config import PROJECT_ROOT, settings
from app.services import etf_metrics, ranking

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

    return templates.TemplateResponse(
        request, "index.html",
        {**_common_ctx(), "sections": sections, "market": market},
    )


@router.get("/etf/{code}", response_class=HTMLResponse)
async def etf_detail(request: Request, code: str) -> HTMLResponse:
    """ETF 詳情頁 — 100% 讀本地 DB,不打外部 API。"""
    detail = etf_metrics.get_etf_detail(code)
    if not detail:
        raise HTTPException(status_code=404, detail=f"找不到 ETF: {code}")
    return templates.TemplateResponse(
        request, "etf_detail.html",
        {**_common_ctx(), "etf": detail},
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
