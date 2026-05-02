"""Authlib OAuth client setup — Google only。

紀律:
- client_id / secret 從 settings 讀,不寫死
- state 參數由 authlib 自動產生 + 驗證(CSRF 防護)
- scope = openid + email + profile,不要任何 calendar / drive 等多餘權限
"""
from __future__ import annotations

import logging

from authlib.integrations.starlette_client import OAuth

from app.config import settings

logger = logging.getLogger(__name__)


def is_google_oauth_enabled() -> bool:
    """OAuth 功能是否已啟用 — 兩個 credential 都填了才算。"""
    return bool(settings.google_client_id and settings.google_client_secret)


oauth = OAuth()

if is_google_oauth_enabled():
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    logger.info("[oauth] Google OAuth client registered")
else:
    logger.info("[oauth] Google OAuth credentials not set — login disabled")
