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
from app.services.time_utils import humanize_relative, is_fresh_news

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

# 紀律 #16 — server-side relative time,JS 失效時 SSR 已正確,且時區走 Asia/Taipei 不漂
templates.env.filters["humanize_relative"] = humanize_relative
templates.env.filters["is_fresh_news"] = is_fresh_news


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


# 首頁 60s memory cache — 紀律 #16,Tokyo→Taipei p50 920ms→~50ms
# 首頁 7 個 heavy block(market overview + 6 sections + dividends + news)= 800+ms
# server compute,但內容只有 14:30 sync 後才會變,60s TTL 對使用者體感無感
# 但對 server 是 16 倍負擔削減。stampede 容忍 — 同時 N 個 cache miss 各自跑
# 一次,寫回 last-wins,後續全 hit。不上 lock 換簡潔。
import time as _time

_INDEX_CACHE: dict = {"data": None, "expires_at": 0.0}
_INDEX_CACHE_TTL = 60.0

# 紀律 #16 — 通用 TTL cache。/compare 與 /ranking 同樣 read-heavy + 算 ranking
# 重,搬同套 cache pattern 過來。鍵用 tuple,值存 (expires_at, payload)。
# 無 lock(同首頁 cache 設計),stampede 容忍。
_ENDPOINT_CACHE: dict[tuple, tuple[float, object]] = {}
_ENDPOINT_TTL = 60.0


def _ttl_cached(key: tuple, build_fn):
    """Get-or-build with 60s TTL,key 必須 hashable。"""
    now = _time.monotonic()
    entry = _ENDPOINT_CACHE.get(key)
    if entry is not None and now < entry[0]:
        return entry[1]
    val = build_fn()
    _ENDPOINT_CACHE[key] = (now + _ENDPOINT_TTL, val)
    return val


def _build_index_payload() -> dict:
    """heavy 部分(market / sections / dividends / news)— 給 cache wrapper 包。"""
    try:
        market = ranking.get_market_overview()
    except Exception:
        logger.exception("[index] market_overview failed")
        market = None

    # Phase 2A: 6 類別卡 2x3,每張 Top 5
    sections = []
    try:
        sections.append({
            "kind": "top_movers",
            "category": "top",
            "title": "近月最火 ETF",
            "subtitle": "不分類別,看誰最近表現最強",
            "data": ranking.get_top_movers("1m", limit=5),
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
                "data": ranking.get_ranking(cat_code, "3m", limit=5),
            })
        except Exception:
            logger.exception("[index] category %s failed", cat_code)
            sections.append({"kind": "category", "category": cat_code, "title": cat_label, "data": None})

    for direction, kind, title in [
        ("positive", "leverage_pos", "槓桿型(高風險)"),
        ("inverse",  "leverage_neg", "反向型(高風險)"),
    ]:
        try:
            sections.append({
                "kind": kind,
                "category": kind,
                "title": title,
                "data": ranking.get_leverage_ranking("3m", direction, limit=5),
            })
        except Exception:
            logger.exception("[index] leverage %s failed", direction)
            sections.append({"kind": kind, "category": kind, "title": title, "data": None})

    # Phase 1B 配息公布欄 — 未來 14 天「即將除息」
    try:
        upcoming = dividend_metrics.get_upcoming_dividends(days=14, past_days=0)
    except Exception:
        logger.exception("[index] upcoming_dividends failed")
        upcoming = None

    # Phase 2A 新聞 5 則(主流類別,近 30 天才有可能有東西)
    try:
        latest_news = news_sync.list_recent_news(limit=5, days=30)
    except Exception:
        logger.exception("[index] latest_news failed")
        latest_news = []

    return {
        "sections": sections,
        "market": market,
        "upcoming_dividends": upcoming,
        "upcoming_group_labels": dividend_metrics.GROUP_LABELS,
        "latest_news": latest_news,
    }


def _get_index_payload() -> dict:
    """60s TTL cache wrapper。每進程獨立(reload 後 cold,正常)。"""
    now = _time.monotonic()
    if _INDEX_CACHE["data"] is not None and now < _INDEX_CACHE["expires_at"]:
        return _INDEX_CACHE["data"]
    payload = _build_index_payload()
    _INDEX_CACHE["data"] = payload
    _INDEX_CACHE["expires_at"] = now + _INDEX_CACHE_TTL
    return payload


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """首頁 — Phase 2A 瘦身版:hero / 市場概況 / 配息公布欄 / 6 類別卡 2x3 / 新聞 5 則。

    TTL=60s memory cache(紀律 #16):heavy 部分 cache,brand / today_iso 即時。
    """
    payload = _get_index_payload()
    return templates.TemplateResponse(
        request, "index.html",
        {
            **_common_ctx(),
            **payload,
            "today_iso": date.today().isoformat(),
        },
    )


def _parse_date(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return default


def _build_compare_payload(code_tuple: tuple[str, ...], start_date: date, end_date: date) -> dict:
    """Heavy compute for /compare — performance.compare_etfs + dict assembly."""
    from dataclasses import asdict
    code_list = list(code_tuple)
    result = performance.compare_etfs(code_list, start_date, end_date)
    stats_dicts = [asdict(s) for s in result["stats"]]
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
    return {
        "result": {**result, "stats": stats_dicts},
        "form": {
            "codes": ",".join(code_list),
            "codes_list": codes_list,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
    }


@router.get("/compare", response_class=HTMLResponse)
async def compare(
    request: Request,
    codes: str = "",
    start: str | None = None,
    end: str | None = None,
) -> HTMLResponse:
    """績效比較頁 — 自選 ETF + 自選日期區間 + 統計表 + 累積報酬走勢圖。

    TTL=60s memory cache(key = sorted code tuple + start + end)— 熱門組合
    例如預設 0050 / 0050+0056 等命中率高,server compute 從 ~50ms 降到 <1ms。
    """
    today = date.today()
    end_date = _parse_date(end, today)
    start_date = _parse_date(start, today - timedelta(days=365))
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    # 上限 6 支(避免圖表太擠);保留輸入順序但 cache key 用排序版避免 0050,0056 / 0056,0050 命中分裂
    code_list = [c.strip().upper() for c in codes.split(",") if c.strip()][:6]
    cache_key = ("compare", tuple(sorted(code_list)), start_date.isoformat(), end_date.isoformat())
    payload = _ttl_cached(cache_key, lambda: _build_compare_payload(tuple(code_list), start_date, end_date))

    return templates.TemplateResponse(
        request, "compare.html",
        {
            **_common_ctx(),
            **payload,
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


def _parse_ym(ym: str | None, today: date) -> tuple[int, int]:
    """ym = "YYYY-MM",fallback 當前月。"""
    if ym:
        try:
            y, m = ym.split("-", 1)
            yi, mi = int(y), int(m)
            if 1 <= mi <= 12 and 1900 <= yi <= 2999:
                return yi, mi
        except (ValueError, TypeError):
            pass
    return today.year, today.month


def _month_range(year: int, month: int) -> tuple[date, date, int, int]:
    """傳回 (first_day, last_day, prev_y, prev_m, next_y, next_m) 的前 4 個。

    為了同時拿到鄰月導覽,call site 可依需要再算。
    """
    import calendar
    first = date(year, month, 1)
    last_day_num = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day_num)
    return first, last, year, month


@router.get("/dividend-calendar", response_class=HTMLResponse)
async def dividend_calendar(
    request: Request,
    ym: str | None = None,
    mode: str = "cal",
) -> HTMLResponse:
    """配息日曆 — 月曆 / 列表雙模式(Phase 3)。

    URL: /dividend-calendar?ym=2026-04&mode=cal
    mode: cal(月曆) / list(列表)
    """
    import calendar as _cal
    today = date.today()
    year, month = _parse_ym(ym, today)
    first, last, _, _ = _month_range(year, month)

    # 鄰月導覽
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)

    # 本月所有配息事件
    try:
        events = dividend_metrics.get_dividends_in_range(first, last)
    except Exception:
        logger.exception("[dividend_calendar] failed ym=%s-%s", year, month)
        events = []

    # 按日期分組(月曆 + 列表共用)
    events_by_day: dict[str, list] = {}
    for e in events:
        events_by_day.setdefault(e["ex_date"], []).append(e)

    # 月曆網格 — 6 列 x 7 欄(週一起算,週日為一週終點符合台灣習慣)
    # 我們用週日為 col 0(更符合台灣月曆習慣)
    cal = _cal.Calendar(firstweekday=6)   # 6 = Sunday
    weeks: list[list[dict | None]] = []
    for week in cal.monthdatescalendar(year, month):
        row: list[dict | None] = []
        for day in week:
            if day.month != month:
                row.append(None)   # 鄰月空格
                continue
            iso = day.isoformat()
            row.append({
                "iso": iso,
                "day": day.day,
                "is_today": (day == today),
                "weekday": day.weekday(),   # 0=Mon..6=Sun
                "events": events_by_day.get(iso, []),
            })
        weeks.append(row)

    return templates.TemplateResponse(
        request, "dividend_calendar.html",
        {
            **_common_ctx(),
            "year": year,
            "month": month,
            "mode": mode if mode in ("cal", "list") else "cal",
            "weeks": weeks,
            "events": events,
            "events_by_day": events_by_day,
            "total_events": len(events),
            "prev_ym": f"{prev_y:04d}-{prev_m:02d}",
            "next_ym": f"{next_y:04d}-{next_m:02d}",
            "today_ym": f"{today.year:04d}-{today.month:02d}",
            "today_iso": today.isoformat(),
            "is_current_month": (year == today.year and month == today.month),
        },
    )


_RANKING_KIND_LABEL: dict[str, dict] = {
    "top":          {"label": "近月最火 ETF", "subtitle": "不分類別,看誰最近表現最強"},
    "active":       {"label": "主動式 ETF", "subtitle": "基金經理人選股,可彈性調整持股"},
    "market":       {"label": "市值型 ETF", "subtitle": "買到台股市值最大的公司"},
    "dividend":     {"label": "高股息 ETF", "subtitle": "主打配息,適合存股族"},
    "theme":        {"label": "主題型 ETF", "subtitle": "鎖定特定產業(半導體 / AI / 5G 等),電腦按指數選股"},
    "bond":         {"label": "債券型 ETF", "subtitle": "公債、公司債,波動較小"},
    "leverage_pos": {"label": "槓桿型 ETF(高風險)", "subtitle": "短線操作工具,長期持有可能因波動衰減而虧損"},
    "leverage_neg": {"label": "反向型 ETF(高風險)", "subtitle": "短線操作工具,長期持有可能因波動衰減而虧損"},
}

_RANKING_PERIODS = ("1m", "3m", "ytd", "1y", "3y")


@router.get("/ranking/{kind}", response_class=HTMLResponse)
async def ranking_detail(request: Request, kind: str, p: str = "3m") -> HTMLResponse:
    """各類別獨立排行頁(Phase 2B)— 完整 Top 30 + 多期間 tab。

    URL: /ranking/{kind}?p=3m
    kind: top / active / market / dividend / theme / bond / leverage_pos / leverage_neg
    p:    1m / 3m / ytd / 1y / 3y
    """
    if kind not in _RANKING_KIND_LABEL:
        raise HTTPException(status_code=404, detail=f"unknown ranking kind: {kind}")
    if p not in _RANKING_PERIODS:
        p = "3m"

    meta = _RANKING_KIND_LABEL[kind]
    LIMIT = 30

    def _build():
        try:
            if kind == "top":
                return ranking.get_top_movers(p, limit=LIMIT)
            if kind == "leverage_pos":
                return ranking.get_leverage_ranking(p, "positive", limit=LIMIT)
            if kind == "leverage_neg":
                return ranking.get_leverage_ranking(p, "inverse", limit=LIMIT)
            return ranking.get_ranking(kind, p, limit=LIMIT)
        except Exception:
            logger.exception("[ranking_detail] failed kind=%s p=%s", kind, p)
            return None

    # 8 kinds × 5 periods = 40 個 cache 槽,熱命中率高
    data = _ttl_cached(("ranking", kind, p, LIMIT), _build)

    return templates.TemplateResponse(
        request, "ranking_detail.html",
        {
            **_common_ctx(),
            "kind": kind,
            "kind_label": meta["label"],
            "kind_subtitle": meta["subtitle"],
            "period": p,
            "periods": _RANKING_PERIODS,
            "period_labels": ranking.PERIOD_LABELS,
            "data": data,
            "is_leverage": kind in ("leverage_pos", "leverage_neg"),
        },
    )


@router.get("/visual-preview", response_class=HTMLResponse)
async def visual_preview_page(request: Request) -> HTMLResponse:
    """臨時視覺風格預覽頁(A/B/C 三選一,選完即廢)。"""
    return templates.TemplateResponse(
        request, "visual_preview.html", _common_ctx(),
    )


@router.get("/monthly-income", response_class=HTMLResponse)
async def monthly_income_page(request: Request) -> HTMLResponse:
    """月月配試算器頁面 — 100% 前端 + AJAX 打 /api/monthly-income/analyze。"""
    return templates.TemplateResponse(
        request, "monthly_income.html", _common_ctx(),
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


@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "contact.html", _common_ctx())


@router.get("/changelog", response_class=HTMLResponse)
async def changelog(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "changelog.html", _common_ctx())


@router.get("/disclaimer", response_class=HTMLResponse)
async def disclaimer(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/disclaimer.html", _common_ctx())


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/terms.html", _common_ctx())


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/privacy.html", _common_ctx())
