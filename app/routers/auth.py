"""Google OAuth login / callback / logout routes."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.auth.oauth import is_google_oauth_enabled, oauth
from app.database import session_scope
from app.models.user import User
from app.services.share_service import (
    COOKIE_REF_CODE,
    generate_referral_code,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google/login")
async def google_login(request: Request):
    """跳轉 Google OAuth 授權頁。authlib 自動產生 state 寫進 session 防 CSRF。"""
    if not is_google_oauth_enabled():
        raise HTTPException(503, "Google OAuth 未設定")

    redirect_uri = str(request.url_for("google_callback"))
    # 紀律 #16:Zeabur 部署在 Cloudflare 後,outer scheme 應該是 https。
    # request.url_for 在 ASGI scope 偶爾回 http(忽略 X-Forwarded-Proto),
    # 強制改 https 避免 redirect_uri mismatch
    if request.url.hostname not in ("localhost", "127.0.0.1") and redirect_uri.startswith("http://"):
        redirect_uri = "https://" + redirect_uri[len("http://"):]
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request):
    """Google 回呼 — 換 token / 拿 userinfo / UPSERT user / 設 session。"""
    if not is_google_oauth_enabled():
        raise HTTPException(503, "Google OAuth 未設定")

    # user 拒絕授權 / Google 回 error → 跳首頁不噴錯
    if "error" in request.query_params:
        logger.info("[auth] user denied: %s", request.query_params.get("error"))
        return RedirectResponse(url="/?login=cancelled", status_code=302)

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        # state mismatch / 過期 / 任何 exchange 失敗 → 不噴 stack 給 user 看
        logger.warning("[auth] token exchange failed: %s", type(e).__name__)
        return RedirectResponse(url="/?login=failed", status_code=302)

    info = token.get("userinfo") or {}
    google_id = info.get("sub")
    email = info.get("email")
    if not google_id or not email:
        logger.warning("[auth] missing sub/email in userinfo")
        return RedirectResponse(url="/?login=failed", status_code=302)

    display_name = info.get("name") or (email.split("@")[0] if email else None)
    avatar_url = info.get("picture")
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)

    # UPSERT — 同 google_id 第二次登入只更新 display_name / avatar_url / last_login_at。
    # 同步確保 referral_code 存在(舊 user 沒 backfill 到的也補洞)。
    user_ref_code: str | None = None
    with session_scope() as s:
        existing = s.scalar(select(User).where(User.google_id == google_id))
        if existing:
            existing.email = email
            existing.display_name = display_name
            existing.avatar_url = avatar_url
            existing.last_login_at = now
            if not existing.referral_code:
                # 舊用戶補洞:6 字元 [A-Z0-9],撞了 retry
                for _ in range(20):
                    code = generate_referral_code()
                    dup = s.scalar(
                        select(User).where(User.referral_code == code)
                    )
                    if not dup:
                        existing.referral_code = code
                        break
            s.flush()
            user_id = existing.id
            user_ref_code = existing.referral_code
        else:
            # 新用戶 — 直接產生 referral_code
            for _ in range(20):
                code = generate_referral_code()
                dup = s.scalar(select(User).where(User.referral_code == code))
                if not dup:
                    break
            else:
                code = None
            new_user = User(
                google_id=google_id,
                email=email,
                display_name=display_name,
                avatar_url=avatar_url,
                last_login_at=now,
                referral_code=code,
            )
            s.add(new_user)
            s.flush()
            user_id = new_user.id
            user_ref_code = new_user.referral_code

    # 紀律:log 只印 user_id,不印 email
    logger.info("[auth] login success user_id=%s", user_id)

    # 寫進 session(SessionMiddleware 簽章後寫進 cookie)
    request.session["user_id"] = user_id

    # 跳回首頁 + 把 referral_code 寫進 cookie 給 JS 讀(分享按鈕拼 ?ref=XXX 用)
    # HttpOnly=False:JS 必須讀(這不是敏感 token,只是用戶 ID 別名)
    resp = RedirectResponse(url="/", status_code=302)
    if user_ref_code:
        resp.set_cookie(
            key=COOKIE_REF_CODE,
            value=user_ref_code,
            max_age=365 * 24 * 3600,  # 1 年
            samesite="lax",
            httponly=False,
        )
    return resp


@router.post("/logout")
async def logout(request: Request):
    """清掉 session 內的 user_id。POST + form 觸發,降低 CSRF 風險。"""
    user_id = request.session.pop("user_id", None)
    if user_id:
        logger.info("[auth] logout user_id=%s", user_id)
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie(COOKIE_REF_CODE, path="/")
    return resp
