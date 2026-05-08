"""Auth middleware — 讀 session cookie → 撈 user → 掛 request.state.user。

放在 SessionMiddleware 之後執行(SessionMiddleware 解 cookie → request.session 才有值)。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from starlette.types import ASGIApp, Receive, Scope, Send

from app.database import session_scope
from app.models.user import User

logger = logging.getLogger(__name__)


class CurrentUserMiddleware:
    """純 ASGI middleware — 從 request.session['user_id'] 撈 User → request.state.user。

    注意:必須掛在 SessionMiddleware 之後(LIFO 註冊順序看起來像在前面)。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # SessionMiddleware 已 populate scope["session"] dict
        sess = scope.get("session") or {}
        user_id = sess.get("user_id")
        scope.setdefault("state", {})
        scope["state"]["user"] = None

        if user_id:
            try:
                with session_scope() as s:
                    user = s.scalar(select(User).where(User.id == int(user_id)))
                    if user:
                        # detach from ORM session — middleware 內 user 物件需要在
                        # request lifetime 內可讀,但不要綁定到 session
                        scope["state"]["user"] = {
                            "id": user.id,
                            "google_id": user.google_id,
                            "email": user.email,
                            "display_name": user.display_name,
                            "avatar_url": user.avatar_url,
                        }
            except Exception:
                logger.exception("[auth] load user failed user_id=%s", user_id)

        await self.app(scope, receive, send)
