"""Paywall 判斷 — is_sponsor / trial_status / ref_token。

紀律 #14:輕量(SQLite < 1ms),不加 cache。
匿名訪客 → user None → is_sponsor=False(不查 DB)。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
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


def _is_admin_email(user: Any) -> bool:
    """判斷 user.email 是否在 ADMIN_EMAILS 白名單。"""
    if not user:
        return False
    email = user.get("email") if isinstance(user, dict) else getattr(user, "email", None)
    if not email:
        return False
    raw = (settings.admin_email or "").lower()
    if not raw:
        return False
    return email.lower() in {e.strip() for e in raw.split(",") if e.strip()}


def is_sponsor(user: Any) -> bool:
    """判斷是否解鎖進階(admin / 付費 / 試用中)。"""
    if not user:
        return False
    # admin 直接視為 sponsor(看完整內容)
    if _is_admin_email(user):
        return True
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
    status: guest / free / trial / premium / admin
    """
    if not user:
        return {"status": "guest", "until": None}
    if _is_admin_email(user):
        return {"status": "premium", "until": None, "admin": True}
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


def grant_trial_check(referrer_user_id: int, ref_token: str,
                      visitor_ip: str | None, visitor_ua: str | None) -> dict:
    """訪客點 ref 連結時,給 referrer 加 2 天試用(24h 冷卻)。

    Returns:
        {granted: bool, reason: str, trial_until: iso or None}
    """
    now = datetime.now()
    with session_scope() as session:
        plan = session.scalar(select(UserPlan).where(UserPlan.user_id == referrer_user_id))
        if plan is None:
            plan = UserPlan(user_id=referrer_user_id, current_plan="free")
            session.add(plan)
            session.flush()

        # 24h 冷卻檢查
        if plan.last_share_at and (now - plan.last_share_at) < timedelta(hours=24):
            # 寫 referrals row 但 reward_granted=False
            from app.models.billing import Referral
            session.add(Referral(
                referrer_user_id=referrer_user_id,
                ref_token=ref_token[:32],
                visitor_ip=(visitor_ip or "")[:64],
                visitor_user_agent=(visitor_ua or "")[:512],
                clicked_at=now,
                reward_granted=False,
                granted_at=None,
            ))
            return {"granted": False, "reason": "24h cooldown", "trial_until": None}

        # 給 2 天試用(疊加在既有 trial_until 之後)
        base = plan.trial_until if (plan.trial_until and plan.trial_until > now) else now
        plan.trial_until = base + timedelta(days=2)
        plan.last_share_at = now
        plan.total_share_count = (plan.total_share_count or 0) + 1
        plan.pending_notification = True

        # 記錄 referrals
        from app.models.billing import Referral
        session.add(Referral(
            referrer_user_id=referrer_user_id,
            ref_token=ref_token[:32],
            visitor_ip=(visitor_ip or "")[:64],
            visitor_user_agent=(visitor_ua or "")[:512],
            clicked_at=now,
            reward_granted=True,
            granted_at=now,
        ))
        trial_iso = plan.trial_until.isoformat()
    logger.info("[paywall.grant_trial] +2d for user %d → %s", referrer_user_id, trial_iso)
    return {"granted": True, "reason": "ok", "trial_until": trial_iso}


def clear_pending_notification(user_id: int) -> None:
    """user 看到 toast 後 client 呼叫,清掉 flag。"""
    with session_scope() as session:
        plan = session.scalar(select(UserPlan).where(UserPlan.user_id == user_id))
        if plan and plan.pending_notification:
            plan.pending_notification = False


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
