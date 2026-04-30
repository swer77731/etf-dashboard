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

@router.get("", response_class=HTMLResponse)
async def admin_root(request: Request):
    if _is_authed(request):
        return RedirectResponse(url="/admin/analytics", status_code=302)
    return RedirectResponse(url="/admin/login", status_code=302)


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

        # 額外:總 session、總 PV 給 sanity check
        totals = s.execute(sql_text("""
            SELECT
                COUNT(DISTINCT session_id) AS total_sessions,
                COUNT(*) AS total_pv
            FROM analytics_log
            WHERE date(ts) = date('now')
        """)).one()

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
</style>
</head>
<body class="px-4 sm:px-6 py-6 max-w-6xl mx-auto">
  <header class="mb-6 flex items-center justify-between">
    <div>
      <h1 class="text-xl font-semibold">Bot 診斷 · 今日</h1>
      <div class="text-sm text-gray-400 mt-1">
        總 session = <span class="num">{totals.total_sessions}</span> ·
        總 PV = <span class="num">{totals.total_pv}</span>
      </div>
    </div>
    <a href="/admin/analytics" class="text-sm text-gray-400 hover:text-white">← 回 Analytics</a>
  </header>

  <div class="card">
    <h2>1. 同 session 訪問次數分布(快看)</h2>
    <table>
      <thead><tr><th>區間</th><th class="text-right">session 數</th></tr></thead>
      <tbody>{bucket_html}</tbody>
    </table>
    <p class="text-xs text-gray-500 mt-3">
      正常人類 1-20 頁;100+ 是 bot / 爬蟲特徵。
    </p>
  </div>

  <div class="card">
    <h2>2. UA 分布(Top 30,按 session 數排)</h2>
    <table>
      <thead><tr><th>User-Agent</th><th class="text-right">sessions</th><th class="text-right">PV</th></tr></thead>
      <tbody>{ua_html}</tbody>
    </table>
    <p class="text-xs text-gray-500 mt-3">
      看到 bot/crawler/spider/Googlebot/UptimeRobot/python-requests/curl 等就是 bot。
    </p>
  </div>

  <div class="card">
    <h2>3. 同 IP 開超多 session(bot 特徵 — 真人 1-3 個就頂)</h2>
    <table>
      <thead><tr><th>IP(末段已遮)</th><th class="text-right">sessions</th><th class="text-right">PV</th></tr></thead>
      <tbody>{ip_html}</tbody>
    </table>
  </div>

  <p class="text-xs text-gray-500 mt-6">
    截圖回給 Claude(或回報前 5 列 UA + Top 5 IP),決定要不要加 bot filter middleware。
  </p>
</body>
</html>"""
    return HTMLResponse(content=html)
