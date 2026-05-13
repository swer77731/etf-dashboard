"""Paywall 判斷 — is_sponsor / trial_status / ref_token。

紀律 #14:輕量(SQLite < 1ms),不加 cache。
匿名訪客 → user None → is_sponsor=False(不查 DB)。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from app.config import settings
from app.database import session_scope
from app.models.billing import UserPlan

logger = logging.getLogger(__name__)


def _user_id_from(user: Any) -> Optional[int]:
    """從各種 user 物件(dict / ORM / state)拿 id。"""
    if not user:
        return None
    if isinstance(user, dict):
        return user.get("id") or user.get("user_id")
    return getattr(user, "id", None)


def get_user_plan(user_id: int) -> Optional[UserPlan]:
    if not user_id:
        return None
    with session_scope() as session:
        plan = session.scalar(select(UserPlan).where(UserPlan.user_id == user_id))
        if plan is not None:
            # detach by reading attrs(SessionScope 結束時 session close)
            session.expunge(plan)
        return plan


def ensure_user_plan(user_id: int) -> UserPlan:
    """取或建 UserPlan(預設 free)。"""
    with session_scope() as session:
        plan = session.scalar(select(UserPlan).where(UserPlan.user_id == user_id))
        if plan is None:
            plan = UserPlan(user_id=user_id, current_plan="free")
            session.add(plan)
            session.flush()
        session.expunge(plan)
        return plan


def is_sponsor(user: Any) -> bool:
    """判斷是否解鎖進階(付費 OR 試用中)。"""
    uid = _user_id_from(user)
    if not uid:
        return False
    plan = get_user_plan(uid)
    if plan is None:
        return False
    now = datetime.now()
    if plan.premium_until and plan.premium_until > now:
        return True
    if plan.trial_until and plan.trial_until > now:
        return True
    return False


def get_trial_status(user: Any) -> dict:
    """UI 顯示用:{status, until}。
    status: guest / free / trial / premium
    """
    uid = _user_id_from(user)
    if not uid:
        return {"status": "guest", "until": None}
    plan = get_user_plan(uid)
    if plan is None:
        return {"status": "free", "until": None}
    now = datetime.now()
    if plan.premium_until and plan.premium_until > now:
        return {"status": "premium", "until": plan.premium_until.isoformat()}
    if plan.trial_until and plan.trial_until > now:
        return {"status": "trial", "until": plan.trial_until.isoformat()}
    return {"status": "free", "until": None}


def get_or_create_ref_token(user_id: int) -> str:
    """產生 user 專屬 ref_token(deterministic,固定一輩子)。

    用 SECRET_KEY + user_id 做 sha256[:12]。同 user 永遠同 token。
    """
    raw = f"{user_id}:{settings.session_secret_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def find_referrer_by_token(token: str) -> Optional[int]:
    """從 ref_token 反推 referrer user_id(掃所有 user)。

    用法:訪客帶 ?ref=xxx,我們要找這 token 對應哪個 user。
    紀律 #14 簡單實作:遍歷 user_plans + compare token(數量小,SQLite OK)。
    User 數量大時改用 referrals 表反查或加 ref_token 欄位到 user。
    """
    if not token or len(token) != 12:
        return None
    with session_scope() as session:
        # 拿所有 user_id 比對
        user_ids = list(session.scalars(select(UserPlan.user_id)))
    for uid in user_ids:
        if get_or_create_ref_token(uid) == token:
            return uid
    return None
