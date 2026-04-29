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
