"""後台 — /admin/login + /admin/analytics(JWT cookie 驗證)。

紀律 #16:
- ADMIN_PASSWORD 是占位值 CHANGE_ME 時直接拒登(避免被人猜)
- JWT 用 settings.secret_key 簽,7 天有效,HttpOnly cookie
- secret_key 還是預設 change-me-in-production 時也擋(production 一定要設)
- 失敗 sleep 0.5s 防 timing attack
- 全中文 UI,跟 dashboard 暗色一致
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt

from app.config import PROJECT_ROOT, settings
from app.services import admin_analytics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

ADMIN_COOKIE = "etfw_admin"
JWT_ALG = "HS256"
JWT_TTL_DAYS = 7

# 沒有 settings.app_brand_full 等於 user 沒設定 → fallback;但 _common_ctx 在 pages.py
# 不想 import 拉出來只給 admin 用,直接走最小 ctx
def _admin_ctx(request: Request, **extra) -> dict:
    return {
        "request": request,
        "brand_full": settings.app_brand_full,
        "brand_zh": settings.app_name,
        **extra,
    }


def _admin_disabled_reason() -> str | None:
    """admin 功能能不能用?回原因字串(disabled)or None(OK)。"""
    if not settings.admin_password or settings.admin_password == "CHANGE_ME":
        return "ADMIN_PASSWORD 未設定(或仍是占位值 CHANGE_ME)"
    if not settings.secret_key or settings.secret_key == "change-me-in-production":
        return "SECRET_KEY 未設定"
    return None


def _make_token() -> str:
    payload = {
        "iat": datetime.now(tz=timezone.utc),
        "exp": datetime.now(tz=timezone.utc) + timedelta(days=JWT_TTL_DAYS),
        "scope": "admin",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALG)


def _verify_token(token: str) -> bool:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALG])
        return payload.get("scope") == "admin"
    except JWTError:
        return False


def _is_authed(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        return False
    return _verify_token(token)


# ─────────────────────────────────────────────────────────────
# /admin → redirect 看狀態
# ─────────────────────────────────────────────────────────────

def _admin_emails() -> set[str]:
    """settings.admin_email 解析成 set(逗號分隔,小寫)。空 = set()(沒人能進)。"""
    raw = (settings.admin_email or "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _is_site_admin(request: Request) -> tuple[bool, dict | None]:
    """檢查 request.state.user(由 CurrentUserMiddleware 注入)是否為站長。

    回 (is_admin, user_dict | None)
    - user None → 還沒登入
    - user.email 不在 admin_email 白名單 → not admin
    - 在白名單 → admin
    """
    user = getattr(request.state, "user", None)
    if not user or not user.get("email"):
        return False, None
    if user["email"].lower() in _admin_emails():
        return True, user
    return False, user


def _mask_email(email: str) -> str:
    """sw***@gmail.com 樣式。前 2 字 + *** + @domain。"""
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local}***@{domain}"
    return f"{local[:2]}***@{domain}"


def _member_stats() -> dict:
    """聚合會員數據 — 跑一次 SQL 查全部需要的數字。

    日期界線用 Asia/Taipei 時區的「今天 / 本週(週一)/ 本月」。
    """
    from sqlalchemy import select, func
    from datetime import date, datetime, timedelta, timezone
    from app.database import session_scope
    from app.models.user import User

    # Asia/Taipei 今天的 00:00 換回 UTC naive(User.created_at 是 UTC naive)
    tz = timezone(timedelta(hours=8))
    now_taipei = datetime.now(tz=tz)
    today_start_taipei = now_taipei.replace(hour=0, minute=0, second=0, microsecond=0)
    # 本週起點:週一 00:00(weekday(): Mon=0, Sun=6)
    week_start_taipei = today_start_taipei - timedelta(days=now_taipei.weekday())
    # 本月起點:1 號 00:00
    month_start_taipei = today_start_taipei.replace(day=1)

    def _to_naive_utc(dt_taipei):
        return dt_taipei.astimezone(timezone.utc).replace(tzinfo=None)

    today_utc = _to_naive_utc(today_start_taipei)
    week_utc = _to_naive_utc(week_start_taipei)
    month_utc = _to_naive_utc(month_start_taipei)

    with session_scope() as s:
        total = s.scalar(select(func.count()).select_from(User)) or 0
        today_n = s.scalar(
            select(func.count()).select_from(User).where(User.created_at >= today_utc)
        ) or 0
        week_n = s.scalar(
            select(func.count()).select_from(User).where(User.created_at >= week_utc)
        ) or 0
        month_n = s.scalar(
            select(func.count()).select_from(User).where(User.created_at >= month_utc)
        ) or 0
        recent = s.scalars(
            select(User).order_by(User.created_at.desc()).limit(10)
        ).all()
        recent_list = [
            {
                "email_masked": _mask_email(u.email),
                "display_name": u.display_name or "—",
                "created_at": u.created_at.isoformat(timespec="minutes") if u.created_at else "—",
            }
            for u in recent
        ]

    return {
        "total": total,
        "today": today_n,
        "this_week": week_n,
        "this_month": month_n,
        "recent": recent_list,
    }


@router.get("", response_class=HTMLResponse)
async def admin_root(request: Request):
    """站長後台 — 只有 settings.admin_email 白名單的 Google 帳號能進。

    流程:
    - 未登入 → 跳 Google OAuth 登入
    - 登入但 email 不在白名單 → 403
    - 是站長 → 顯示 dashboard(會員數據,未來追加 DAU/PV/TG)
    """
    is_admin, user = _is_site_admin(request)
    if user is None:
        # 還沒登入 Google → 跳登入,登入後回 /admin
        return RedirectResponse(url="/auth/google/login", status_code=302)
    if not is_admin:
        # 已登入但不是站長
        raise HTTPException(403, "你不是站長,無權進入後台")

    stats = _member_stats()
    ctx = _admin_ctx(request, **stats, current_user=user)
    return templates.TemplateResponse(request, "admin/dashboard.html", ctx)


# ─────────────────────────────────────────────────────────────
# /admin/login
# ─────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        request, "admin/login.html",
        _admin_ctx(request, error=error, disabled_reason=_admin_disabled_reason()),
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    reason = _admin_disabled_reason()
    if reason:
        # 配置不全 → 不准登
        return templates.TemplateResponse(
            request, "admin/login.html",
            _admin_ctx(request, error=f"後台目前停用:{reason}", disabled_reason=reason),
        )
    # constant-time compare 防 timing attack
    ok = secrets.compare_digest(password.encode(), settings.admin_password.encode())
    if not ok:
        time.sleep(0.5)
        return templates.TemplateResponse(
            request, "admin/login.html",
            _admin_ctx(request, error="密碼錯誤", disabled_reason=None),
        )

    resp = RedirectResponse(url="/admin/analytics", status_code=302)
    resp.set_cookie(
        ADMIN_COOKIE, _make_token(),
        max_age=JWT_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return resp


@router.get("/logout")
async def logout(request: Request):
    resp = RedirectResponse(url="/admin/login", status_code=302)
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


# ─────────────────────────────────────────────────────────────
# /admin/analytics
# ─────────────────────────────────────────────────────────────

@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, range_days: int = 7):
    if not _is_authed(request):
        return RedirectResponse(url="/admin/login", status_code=302)

    # range 限制
    if range_days not in (7, 30):
        range_days = 7

    today = admin_analytics.today_taipei_date()
    yesterday = today - timedelta(days=1)

    overview = admin_analytics.overview_with_diff(today, yesterday)
    trend = admin_analytics.dau_trend(days=30)
    capacity = admin_analytics.capacity_overview()
    etfs = admin_analytics.top_etfs(days=range_days, limit=10)
    feats = admin_analytics.top_features(days=range_days, limit=10)
    searches = admin_analytics.top_searches(days=range_days, limit=20)
    compares = admin_analytics.top_compares(days=range_days, limit=10)
    refs = admin_analytics.referer_breakdown(days=range_days)
    visits = admin_analytics.recent_visits(limit=100)

    return templates.TemplateResponse(
        request, "admin/analytics.html",
        _admin_ctx(
            request,
            today_iso=today.isoformat(),
            range_days=range_days,
            overview=overview,
            trend=trend,
            capacity=capacity,
            etfs=etfs,
            feats=feats,
            searches=searches,
            compares=compares,
            refs=refs,
            visits=visits,
        ),
    )


# Debug endpoint — 即時觸發日報(只給已登入 admin 用)
@router.get("/send_daily_report")
async def trigger_daily_report(request: Request):
    if not _is_authed(request):
        raise HTTPException(403, "not admin")
    from app.services import tg_notify
    text = admin_analytics.build_daily_report()
    ok = tg_notify.send_message(text)
    return {"sent": ok, "preview": text}


@router.get("/yearly_returns/backfill")
def trigger_yearly_returns_backfill(request: Request):
    """手動觸發 etf_yearly_returns 全 80 支 backfill(免等 04:00 cron)。

    Zeabur 部署後第一次,或 CSV 變動後重灌名單時用。
    跑 5-10 分鐘(throttle 1s/call,80 calls)。

    重要:寫成 `def`(非 async)讓 FastAPI 把同步 blocking 邏輯
    丟 threadpool 跑,避免吃掉 asyncio event loop 害整站 stalled。
    呼叫端瀏覽器可能 504 timeout,但 server 端會繼續跑完。
    """
    if not _is_authed(request):
        raise HTTPException(403, "not admin")
    from app.database import init_db
    from app.services import yearly_returns_sync
    init_db()
    codes = yearly_returns_sync.load_tracked_codes()
    stats = yearly_returns_sync.sync_all(codes=codes)
    return {
        "expected": stats["expected"],
        "actual": stats["actual"],
        "missing": stats["missing"],
        "total_years_written": stats["total_years_written"],
        "per_code_summary": {
            "with_data": sum(1 for n in stats["per_code"].values() if n > 0),
            "without_data": sum(1 for n in stats["per_code"].values() if n == 0),
        },
    }


# Bot 歷史紀錄清理 — 刪 analytics_log 中 UA 命中黑名單的 row
@router.get("/bot-cleanup", response_class=HTMLResponse)
async def bot_cleanup(request: Request):
    if not _is_authed(request):
        return RedirectResponse(url="/admin/login", status_code=302)

    from sqlalchemy import text as sql_text
    from app.analytics_middleware import BOT_UA_LIKE_PATTERNS, _BOT_UA_PATTERNS
    from app.database import session_scope

    # 動態組 WHERE clause:多個 LIKE OR
    # 注意:user 給的清單只有 analytics_log 有 ua 欄位,search_log/compare_log
    # 沒 ua → 歷史只刪得了 analytics_log。新資料三表都會走 _is_bot_ua 過濾。
    bind_clauses = []
    bind_params = {}
    for i, pat in enumerate(BOT_UA_LIKE_PATTERNS):
        key = f"p{i}"
        bind_clauses.append(f"ua LIKE :{key}")
        bind_params[key] = pat
    # 額外:空 UA 也算 bot
    where_sql = " OR ".join(bind_clauses) + " OR ua IS NULL OR ua = ''"

    with session_scope() as s:
        # Pre-count(預覽要刪幾筆)
        pre_total = s.execute(sql_text("SELECT COUNT(*) FROM analytics_log")).scalar() or 0
        pre_bot = s.execute(
            sql_text(f"SELECT COUNT(*) FROM analytics_log WHERE {where_sql}"),
            bind_params,
        ).scalar() or 0

        # DELETE
        result = s.execute(
            sql_text(f"DELETE FROM analytics_log WHERE {where_sql}"),
            bind_params,
        )
        deleted_analytics = result.rowcount or 0

        # search_log / compare_log 沒 ua 欄位,無法從歷史過濾;只能等新資料
        post_total = s.execute(sql_text("SELECT COUNT(*) FROM analytics_log")).scalar() or 0
        post_dau_today = s.execute(sql_text("""
            SELECT COUNT(DISTINCT session_id) FROM analytics_log
            WHERE date(ts) = date('now')
        """)).scalar() or 0
        post_pv_today = s.execute(sql_text("""
            SELECT COUNT(*) FROM analytics_log
            WHERE date(ts) = date('now')
        """)).scalar() or 0

    return HTMLResponse(content=f"""<!doctype html>
<html lang="zh-Hant" data-theme="dark">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bot 清理結果</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body {{ background:#0a0e1a; color:#e5e7eb; font-family:'Noto Sans TC',ui-sans-serif,system-ui,sans-serif; }}</style>
</head>
<body class="px-4 py-8 max-w-2xl mx-auto">
  <h1 class="text-xl font-semibold mb-4">Bot 清理 完成 ✓</h1>
  <div class="bg-[#131829] border border-[#1f2937] rounded-xl p-5 space-y-3 text-base">
    <div class="flex justify-between"><span class="text-gray-400">清理前 analytics_log 總筆數</span><span class="num font-mono">{pre_total}</span></div>
    <div class="flex justify-between"><span class="text-gray-400">命中 bot UA 條件筆數(預覽)</span><span class="num font-mono text-amber-400">{pre_bot}</span></div>
    <div class="flex justify-between border-t border-[#1f2937] pt-3"><span class="text-gray-400">實際 DELETE rows</span><span class="num font-mono text-red-400">−{deleted_analytics}</span></div>
    <div class="flex justify-between"><span class="text-gray-400">清理後 analytics_log 總筆數</span><span class="num font-mono text-green-400">{post_total}</span></div>
    <div class="flex justify-between border-t border-[#1f2937] pt-3"><span class="text-gray-400">今日訪客數(清理後)</span><span class="num font-mono text-green-400 text-xl">{post_dau_today}</span></div>
    <div class="flex justify-between"><span class="text-gray-400">今日瀏覽次數(清理後)</span><span class="num font-mono text-green-400">{post_pv_today}</span></div>
  </div>
  <div class="mt-6 text-xs text-gray-500 leading-relaxed">
    <p>* 已套 {len(_BOT_UA_PATTERNS)} 個 UA 子字串黑名單 + 空 UA。</p>
    <p>* search_log / compare_log 沒 ua 欄位無法歷史過濾,新資料會被 middleware 擋。</p>
    <p>* 此 endpoint 重複跑安全(已刪過的不會再算)。</p>
  </div>
  <div class="mt-6 flex gap-3 text-sm">
    <a href="/admin/bot-diagnosis" class="text-blue-400 hover:text-blue-300">→ 重看 Bot 診斷</a>
    <a href="/admin/analytics" class="text-blue-400 hover:text-blue-300">→ Analytics</a>
  </div>
</body></html>""")


# Bot 診斷 — 看 DAU 是否被 bot / scraper 灌水
@router.get("/bot-diagnosis", response_class=HTMLResponse)
async def bot_diagnosis(request: Request):
    if not _is_authed(request):
        return RedirectResponse(url="/admin/login", status_code=302)

    from sqlalchemy import text as sql_text
    from app.database import session_scope

    with session_scope() as s:
        # 1. UA 分布(今日)
        ua_rows = s.execute(sql_text("""
            SELECT
                SUBSTR(COALESCE(ua, '(empty)'), 1, 100) AS ua_short,
                COUNT(DISTINCT session_id) AS sessions,
                COUNT(*) AS pv
            FROM analytics_log
            WHERE date(ts) = date('now')
            GROUP BY ua_short
            ORDER BY sessions DESC
            LIMIT 30
        """)).all()

        # 2. 同 IP 開超多 session(bot 特徵)
        ip_rows = s.execute(sql_text("""
            SELECT
                COALESCE(ip_masked, '(null)') AS ip_masked,
                COUNT(DISTINCT session_id) AS sessions,
                COUNT(*) AS pv
            FROM analytics_log
            WHERE date(ts) = date('now')
            GROUP BY ip_masked
            ORDER BY sessions DESC
            LIMIT 20
        """)).all()

        # 3. 單 session 訪問次數分布
        bucket_rows = s.execute(sql_text("""
            SELECT
                CASE
                    WHEN cnt = 1 THEN '1 page'
                    WHEN cnt <= 5 THEN '2-5 pages'
                    WHEN cnt <= 20 THEN '6-20 pages'
                    WHEN cnt <= 100 THEN '21-100 pages'
                    ELSE '100+ pages (very suspicious)'
                END AS bucket,
                COUNT(*) AS session_count
            FROM (
                SELECT session_id, COUNT(*) AS cnt
                FROM analytics_log
                WHERE date(ts) = date('now')
                GROUP BY session_id
            )
            GROUP BY bucket
            ORDER BY MIN(cnt)
        """)).all()

        # 額外:總 session、總 PV 給 sanity check(原始數字,未排除任何 IP)
        totals = s.execute(sql_text("""
            SELECT
                COUNT(DISTINCT session_id) AS total_sessions,
                COUNT(*) AS total_pv
            FROM analytics_log
            WHERE date(ts) = date('now')
        """)).one()

    # 24h 高 session IP 排除清單(/admin/analytics 真實統計用的)
    bot_ips = admin_analytics.get_high_session_ips(window_hours=24)
    threshold = settings.high_session_threshold

    # 簡易 inline HTML(不用 Jinja partial,單頁工具)
    def _td(s, num=False, mono=False):
        cls = []
        if num:
            cls.append('text-right num')
        if mono:
            cls.append('font-mono')
        c = f' class="{" ".join(cls)}"' if cls else ''
        return f"<td{c}>{s}</td>"

    ua_html = "".join(
        f"<tr>{_td(r.ua_short, mono=True)}{_td(r.sessions, num=True)}{_td(r.pv, num=True)}</tr>"
        for r in ua_rows
    )
    ip_html = "".join(
        f"<tr>{_td(r.ip_masked, mono=True)}{_td(r.sessions, num=True)}{_td(r.pv, num=True)}</tr>"
        for r in ip_rows
    )
    bucket_html = "".join(
        f"<tr>{_td(r.bucket)}{_td(r.session_count, num=True)}</tr>"
        for r in bucket_rows
    )

    bot_ips_preview = ", ".join(bot_ips[:5]) if bot_ips else "(無)"
    if len(bot_ips) > 5:
        bot_ips_preview += f" ...（共 {len(bot_ips)} 個）"

    html = f"""<!doctype html>
<html lang="zh-Hant" data-theme="dark">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex,nofollow">
<title>Bot 診斷 — 後台</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body {{ background:#0a0e1a; color:#e5e7eb; font-family:'Noto Sans TC',ui-sans-serif,system-ui,sans-serif; }}
  .num {{ font-variant-numeric:tabular-nums; font-family:ui-monospace,monospace; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.88rem; }}
  th {{ text-align:left; padding:0.5rem 0.75rem; border-bottom:1px solid #1f2937; color:#9ca3af; font-weight:500; }}
  td {{ padding:0.5rem 0.75rem; border-bottom:1px solid #1f2937; word-break:break-all; }}
  td.text-right {{ text-align:right; }}
  td.font-mono {{ font-family:ui-monospace,monospace; font-size:0.78rem; }}
  tr:hover td {{ background:#1a2138; }}
  .card {{ background:#131829; border:1px solid #1f2937; border-radius:0.75rem; padding:1.25rem; margin-bottom:1.5rem; }}
  h2 {{ font-size:1.1rem; font-weight:600; margin-bottom:0.75rem; }}
  .banner {{ background:#1f2937; border:1px solid #374151; border-radius:0.5rem;
             padding:0.75rem 1rem; font-size:0.85rem; color:#fbbf24; margin-bottom:1rem; }}
</style>
</head>
<body class="px-4 sm:px-6 py-6 max-w-6xl mx-auto">
  <header class="mb-6 flex items-center justify-between">
    <div>
      <h1 class="text-xl font-semibold">Bot 診斷 · 今日(原始數字,未排除)</h1>
      <div class="text-sm text-gray-400 mt-1">
        總訪問階段 = <span class="num">{totals.total_sessions}</span> ·
        總瀏覽次數 = <span class="num">{totals.total_pv}</span>
      </div>
    </div>
    <a href="/admin/analytics" class="text-sm text-gray-400 hover:text-white">← 回 Analytics</a>
  </header>

  <div class="banner">
    <b>已排除 {len(bot_ips)} 個高訪問階段 IP</b>(24h 內訪問階段 ≥ {threshold},自動視為 bot)<br>
    <span class="text-xs">/admin/analytics 與 TG 日報的 訪客數 / 瀏覽次數 / 排行皆已排除這些 IP。本頁仍顯示原始數字。</span><br>
    <span class="text-xs num font-mono text-gray-300">{bot_ips_preview}</span>
  </div>

  <div class="card">
    <h2>1. 同訪問階段瀏覽次數分布(快看)</h2>
    <table>
      <thead><tr><th>區間</th><th class="text-right">訪問階段數</th></tr></thead>
      <tbody>{bucket_html}</tbody>
    </table>
    <p class="text-xs text-gray-500 mt-3">
      正常人類 1-20 頁;100+ 是 bot / 爬蟲特徵。
    </p>
  </div>

  <div class="card">
    <h2>2. UA 分布(Top 30,按訪問階段數排)</h2>
    <table>
      <thead><tr><th>User-Agent</th><th class="text-right">訪問階段</th><th class="text-right">瀏覽次數</th></tr></thead>
      <tbody>{ua_html}</tbody>
    </table>
    <p class="text-xs text-gray-500 mt-3">
      看到 bot/crawler/spider/Googlebot/UptimeRobot/python-requests/curl 等就是 bot。
    </p>
  </div>

  <div class="card">
    <h2>3. 同 IP 開超多訪問階段(bot 特徵 — 真人 1-3 個就頂)</h2>
    <table>
      <thead><tr><th>IP(末段已遮)</th><th class="text-right">訪問階段</th><th class="text-right">瀏覽次數</th></tr></thead>
      <tbody>{ip_html}</tbody>
    </table>
  </div>

  <p class="text-xs text-gray-500 mt-6">
    截圖回給 Claude(或回報前 5 列 UA + Top 5 IP),決定要不要加 bot filter middleware。
  </p>
</body>
</html>"""
    return HTMLResponse(content=html)
