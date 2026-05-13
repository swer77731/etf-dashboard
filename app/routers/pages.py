"""HTML page routes — server-rendered Jinja2 + HTMX."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.config import PROJECT_ROOT, settings
from app.services import dividend_metrics, etf_metrics, performance, ranking
from app.services.time_utils import humanize_relative, is_fresh_news, tw_time

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

# 紀律 #16 — server-side relative time,JS 失效時 SSR 已正確,且時區走 Asia/Taipei 不漂
templates.env.filters["humanize_relative"] = humanize_relative
templates.env.filters["is_fresh_news"] = is_fresh_news
templates.env.filters["tw_time"] = tw_time


# 2026-05-02:logo 改用 static/icons/logo.svg(新路徑無 CDN cache 歷史)
# 舊 static/img/logo.svg 在 Cloudflare HIT 1 年 immutable,改 bytes 也讀不到新版
# 之後若要再換 logo,可用 query string `?v=N` 強制 reload,或再換新檔名
_LOGO_SVG_NEW = PROJECT_ROOT / "static" / "icons" / "logo.svg"
_LOGO_SVG_LEGACY = PROJECT_ROOT / "static" / "img" / "logo.svg"
_LOGO_PNG = PROJECT_ROOT / "static" / "img" / "logo.png"
# favicon 同理:新路徑 static/icons/favicon.ico,舊 static/img/favicon.ico 仍 fallback
_FAVICON_NEW = PROJECT_ROOT / "static" / "icons" / "favicon.ico"
_FAVICON_LEGACY = PROJECT_ROOT / "static" / "img" / "favicon.ico"


def _detect_brand_assets() -> dict:
    """偵測 LOGO / favicon 是否存在,讓 template 自動切換。

    優先序:static/icons/(新,2026-05-02 PWA package) → static/img/(舊,fallback)
    """
    if _LOGO_SVG_NEW.exists():
        logo_url = "/static/icons/logo.svg"
    elif _LOGO_SVG_LEGACY.exists():
        logo_url = "/static/img/logo.svg"
    elif _LOGO_PNG.exists():
        logo_url = "/static/img/logo.png"
    else:
        logo_url = None

    if _FAVICON_NEW.exists():
        favicon_url = "/static/icons/favicon.ico"
    elif _FAVICON_LEGACY.exists():
        favicon_url = "/static/img/favicon.ico"
    else:
        favicon_url = None

    return {
        "logo_url": logo_url,
        "favicon_url": favicon_url,
        "has_logo": logo_url is not None,
    }


def _show_error_report_for(path: str) -> bool:
    """8 個「有資料頁面」白名單 — 浮動回報按鈕只在這些路徑顯示。

    法律 / 教學 / 後台 / 註冊登入 / 雜訊頁(/news 等)預設 False。
    """
    if path in (
        "/", "/compare", "/dca",
        "/monthly-income", "/dividend-calendar",
        "/market-temp",
    ):
        return True
    if path.startswith("/etf/") or path.startswith("/ranking/"):
        return True
    return False


def _common_ctx(request: Request | None = None) -> dict:
    """共用品牌 context — 所有頁面都會用到。

    若傳入 request,會從 request.state.user(由 CurrentUserMiddleware 注入)
    補 current_user dict 進 ctx。沒有 request 或未登入 → current_user=None。
    show_error_report:依 request.url.path 判斷,白名單 8 頁顯示浮動回報按鈕。
    """
    from app.auth.oauth import is_google_oauth_enabled
    current_user = None
    show_error_report = False
    if request is not None:
        current_user = getattr(request.state, "user", None)
        show_error_report = _show_error_report_for(request.url.path)
    return {
        "app_name": settings.app_name,
        "app_env": settings.app_env,
        "brand_zh": settings.app_name,
        "brand_en": settings.app_brand_en,
        "brand_full": settings.app_brand_full,
        "current_user": current_user,
        "google_oauth_enabled": is_google_oauth_enabled(),
        "show_error_report": show_error_report,
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

# 紀律 #16 — 通用 TTL cache。/compare /ranking /news /etf /dividend 全套同套。
# 鍵用 tuple,值存 (expires_at, payload | html)。無 lock,stampede 容忍。
# /news 渲染後 HTML ~900KB(5000 row × 多欄位)→ 必須上 cap 防 OOM。
_ENDPOINT_CACHE: dict[tuple, tuple[float, object]] = {}
_ENDPOINT_TTL = 60.0
_ENDPOINT_CACHE_MAX = 200


def _evict_expired_if_full():
    """超過 200 個 entry 時清掉所有已過期 entry。
    沒有過期就清最早 expire 的 50 個(LRU-ish by expiration)。"""
    if len(_ENDPOINT_CACHE) <= _ENDPOINT_CACHE_MAX:
        return
    now = _time.monotonic()
    # 先清過期
    expired = [k for k, (exp, _) in _ENDPOINT_CACHE.items() if exp < now]
    for k in expired:
        _ENDPOINT_CACHE.pop(k, None)
    # 還是太多 → 清最快過期的 50 個
    if len(_ENDPOINT_CACHE) > _ENDPOINT_CACHE_MAX:
        oldest = sorted(_ENDPOINT_CACHE.items(), key=lambda kv: kv[1][0])[:50]
        for k, _ in oldest:
            _ENDPOINT_CACHE.pop(k, None)


def _ttl_cached(key: tuple, build_fn, ttl: float | None = None):
    """Get-or-build with TTL(預設 60s),key 必須 hashable。

    ttl 可覆寫(例:配息日曆用 300s)。
    """
    now = _time.monotonic()
    entry = _ENDPOINT_CACHE.get(key)
    if entry is not None and now < entry[0]:
        return entry[1]
    val = build_fn()
    _evict_expired_if_full()
    actual_ttl = ttl if ttl is not None else _ENDPOINT_TTL
    _ENDPOINT_CACHE[key] = (now + actual_ttl, val)
    return val


def _render_cached(key: tuple, template_name: str, ctx_builder, ttl: float | None = None):
    """Cache rendered HTML(預設 60s,可覆寫 ttl)。

    紀律 #16 進階版:除了 payload,連 Jinja render 也 cache。重 template
    (news 5000 row / etf_detail 7 sections / dividend_calendar 月曆網格)
    渲染本身要 50-100ms,只 cache payload 救不到這層。

    使用前提(已驗):templates 不使用 request.url_for / 不引用 request.*,
    HTML 對所有訪客內容相同 → 直接回 cached bytes 安全。

    ctx_builder 回 None → 視為 404(不 cache)。
    回 str → cache 並 return HTMLResponse。
    """
    now = _time.monotonic()
    entry = _ENDPOINT_CACHE.get(key)
    if entry is not None and now < entry[0]:
        cached = entry[1]
        if cached is None:
            return None
        return HTMLResponse(content=cached)

    ctx = ctx_builder()
    if ctx is None:
        # 短 TTL 30s cache None,避免被刷 404 流量打爆
        _evict_expired_if_full()
        _ENDPOINT_CACHE[key] = (now + 30.0, None)
        return None

    html = templates.env.get_template(template_name).render(ctx)
    _evict_expired_if_full()
    actual_ttl = ttl if ttl is not None else _ENDPOINT_TTL
    _ENDPOINT_CACHE[key] = (now + actual_ttl, html)
    return HTMLResponse(content=html)


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

    # 2026-05-06 拿掉首頁槓桿/反向 sections,首頁只留主動 / 市值 / 高股息 + 近月最火
    # /ranking/leverage 與 /ranking/inverse 路由仍保留(從 nav / 直接打網址可進)

    # Phase 1B 配息公布欄 — 未來 14 天「即將除息」
    try:
        upcoming = dividend_metrics.get_upcoming_dividends(days=14, past_days=0)
    except Exception:
        logger.exception("[index] upcoming_dividends failed")
        upcoming = None

    return {
        "sections": sections,
        "market": market,
        "upcoming_dividends": upcoming,
        "upcoming_group_labels": dividend_metrics.GROUP_LABELS,
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
            **_common_ctx(request),
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
            **_common_ctx(request),
            **payload,
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


def _build_dividend_calendar_payload(year: int, month: int) -> dict:
    """Heavy compute for /dividend-calendar — events 按日期分組。

    2026-05-06 純列表化:不再算 6×7 月曆網格,events_by_day 給列表分組顯示用。
    """
    first, last, _, _ = _month_range(year, month)

    try:
        events = dividend_metrics.get_dividends_in_range(first, last)
    except Exception:
        logger.exception("[dividend_calendar] failed ym=%s-%s", year, month)
        events = []

    events_by_day: dict[str, list] = {}
    for e in events:
        events_by_day.setdefault(e["ex_date"], []).append(e)

    return {
        "events": events,
        "events_by_day": events_by_day,
        "total_events": len(events),
    }


@router.get("/market-temp", response_class=HTMLResponse)
async def market_temperature(request: Request) -> HTMLResponse:
    """市場溫度計 — 融資維持率 gauge + 三大法人 + 漲跌家數 / 融資融券 / 借券。

    純讀本地 DB(資料主權鐵律),5 個 sync 服務在各自釋出時間 cron 更新。
    """
    from app.services import market_temp_view
    payload = market_temp_view.build_payload(days=30)
    return templates.TemplateResponse(
        request, "market_temp.html",
        {
            **_common_ctx(request),
            **payload,
        },
    )


@router.get("/dividend-calendar", response_class=HTMLResponse)
async def dividend_calendar(
    request: Request,
    ym: str | None = None,
) -> HTMLResponse:
    """配息日曆 — 純列表版(Phase 3)。

    2026-05-06 月曆模式拿掉(7 欄硬擠在手機 380px 寬讀不出來),改純列表。
    舊書籤的 ?mode=cal / ?mode=list 仍能進此頁(額外 query 被 FastAPI 忽略)。

    URL: /dividend-calendar?ym=2026-04
    TTL=300s rendered-HTML cache。Key 含 today_iso → 跨日「· 今天」標記自動失效。
    """
    today = date.today()
    today_iso = today.isoformat()
    year, month = _parse_ym(ym, today)
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)

    def _build():
        return {
            **_common_ctx(request),
            "year": year,
            "month": month,
            **_build_dividend_calendar_payload(year, month),
            "prev_ym": f"{prev_y:04d}-{prev_m:02d}",
            "next_ym": f"{next_y:04d}-{next_m:02d}",
            "today_ym": f"{today.year:04d}-{today.month:02d}",
            "today_iso": today_iso,
            "is_current_month": (year == today.year and month == today.month),
            "request": request,
        }

    return _render_cached(
        ("div_cal_html", year, month, today_iso),
        "dividend_calendar.html",
        _build,
        ttl=300.0,   # 5 分鐘 — 配息日曆內容只有除息日當天有效,變動慢
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
            **_common_ctx(request),
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
        request, "visual_preview.html", _common_ctx(request),
    )


@router.get("/monthly-income", response_class=HTMLResponse)
async def monthly_income_page(request: Request) -> HTMLResponse:
    """月月配試算器頁面 — 100% 前端 + AJAX 打 /api/monthly-income/analyze。

    年份字串動態(last_full_year = 今年-1),2027 元旦會自動切到 2026。
    """
    today = date.today()
    last_full_year = today.year - 1
    return templates.TemplateResponse(
        request, "monthly_income.html",
        {
            **_common_ctx(request),
            "last_full_year": last_full_year,
            "past_3y_start": last_full_year - 2,
            "past_3y_end": last_full_year,
            "next_year": today.year,
        },
    )


@router.get("/dca", response_class=HTMLResponse)
async def dca_page(request: Request) -> HTMLResponse:
    """定期定額試算工具 — 示範版。

    ETF 清單 / 元資訊由 /api/dca/etf_list + /api/dca/etf_meta 動態提供
    (避免 80 支 inline 在 HTML 拖慢首屏)。
    """
    return templates.TemplateResponse(request, "dca.html", _common_ctx(request))


@router.get("/monthly-income-preview")
async def monthly_income_preview_redirect():
    """舊 preview URL → 301 永久重導到正式版(已合併)。

    保留是因為 user 之前可能分享過 preview URL。
    """
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/monthly-income", status_code=301)


def _build_etf_detail_payload(code: str):
    """Heavy compute for /etf/{code} — 6 期間報酬 + K 棒走勢 + 配息歷史 + 健康度。"""
    detail = etf_metrics.get_etf_detail(code)
    if not detail:
        return None
    # ETF 健康度 — 受益人數 + 規模 兩張卡片
    from app.services import etf_health
    health = etf_health.build_ctx(code.upper())
    return {
        "etf": detail,
        **health,  # has_health_data / holders_card / aum_card
    }


@router.get("/etf/{code}", response_class=HTMLResponse)
async def etf_detail(request: Request, code: str) -> HTMLResponse:
    """ETF 詳情頁 — 100% 讀本地 DB,不打外部 API。

    TTL=60s rendered-HTML cache(key=code)— 詳情頁 etf_detail.html 渲染重
    (7 sections + 配息歷史表 + 走勢圖數據),cache 連 Jinja render 一起省。
    """
    code_norm = code.upper()

    def _build():
        payload = _build_etf_detail_payload(code_norm)
        if payload is None:
            return None
        return {**_common_ctx(request), **payload, "request": request}

    response = _render_cached(("etf_detail_html", code_norm), "etf_detail.html", _build)
    if response is None:
        raise HTTPException(status_code=404, detail=f"找不到 ETF: {code}")
    return response


@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "contact.html", _common_ctx(request))


@router.get("/changelog", response_class=HTMLResponse)
async def changelog(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "changelog.html", _common_ctx(request))


@router.get("/disclaimer", response_class=HTMLResponse)
async def disclaimer(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/disclaimer.html", _common_ctx(request))


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/terms.html", _common_ctx(request))


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/privacy.html", _common_ctx(request))


@router.get("/account-delete", response_class=HTMLResponse)
async def account_delete(request: Request) -> HTMLResponse:
    """Google OAuth 上架要求:公開可訪問、無需登入的帳號刪除說明頁。"""
    return templates.TemplateResponse(request, "legal/account_delete.html", _common_ctx(request))


@router.get("/install", response_class=HTMLResponse)
async def install_guide(request: Request) -> HTMLResponse:
    """PWA 安裝教學 — iOS / Android 步驟說明 + FAQ。"""
    return templates.TemplateResponse(request, "install.html", _common_ctx(request))


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    """SEO sitemap — 公開頁面 + 所有 active ETF 詳情頁。

    AdSense / Googlebot 用此檔發現網站全部可索引頁面。
    """
    from fastapi.responses import Response
    base = "https://etf-watch.com"
    static_pages = [
        "", "/compare", "/dca", "/monthly-income", "/dividend-calendar",
        "/market-temp", "/ranking/top",
        "/disclaimer", "/privacy", "/terms", "/account-delete",
        "/contact", "/changelog", "/install",
    ]
    from sqlalchemy import select
    from app.database import session_scope
    from app.models.etf import ETF
    with session_scope() as session:
        etf_codes = list(session.scalars(
            select(ETF.code).where(ETF.code != "TAIEX", ETF.is_active == True)  # noqa: E712
        ))

    today_iso = date.today().isoformat()
    urls = []
    for path in static_pages:
        urls.append(f'<url><loc>{base}{path}</loc><lastmod>{today_iso}</lastmod></url>')
    for code in etf_codes:
        urls.append(f'<url><loc>{base}/etf/{code}</loc><lastmod>{today_iso}</lastmod></url>')

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>"
    )
    from fastapi.responses import Response as _Resp
    return _Resp(content=xml, media_type="application/xml")


# === PWA 路由(根路徑提供 sw + manifest)===
# Service Worker 必須從根路徑提供才能控制整個 origin(Service-Worker-Allowed: /)
# manifest.json 額外從根提供一份,讓 SW pre-cache 清單裡的 /manifest.json 命中
_STATIC_DIR = PROJECT_ROOT / "static"


@router.get("/service-worker.js", include_in_schema=False)
async def service_worker():
    """根路徑的 SW — 控制整個 origin scope。"""
    return FileResponse(
        str(_STATIC_DIR / "service-worker.js"),
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "public, max-age=0, must-revalidate",  # SW 自身不快取,確保更新即時生效
        },
    )


@router.get("/manifest.json", include_in_schema=False)
async def manifest_root():
    """根路徑同份 manifest — SW pre-cache 清單裡含 /manifest.json,提供 passthrough。
    base.html 仍指向 /static/manifest.json(主路徑),這邊只是相容路徑。"""
    return FileResponse(
        str(_STATIC_DIR / "manifest.json"),
        media_type="application/manifest+json",
    )
