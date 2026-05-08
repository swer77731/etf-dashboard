"""分享 + 推薦核心邏輯。

職責
====
- referral_code 產生器(6 字元 [A-Z0-9])
- 訪客 ?ref=XXX 處理(dedupe + insert + cookie 名稱)
- 分享按鈕點擊紀錄
- 訪客 30s 停留 → mark valid
- ad_free 預埋 helpers(should_show_ad / grant_ad_free_days)— 現在不接 AdSense,
  寫好放著。等廣告上線時模板 + 觸發點再串。

紀律 #18:user_agent / IP 要 hash 後再寫 DB,不留可識別個資。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import session_scope
from app.models.share import ShareButtonClick, ShareClick
from app.models.user import User

logger = logging.getLogger(__name__)

# Cookie 名稱
COOKIE_REF_CLICK_ID = "evw_ref_click"   # share_clicks.id(讓 30s ping 找得到 row)
COOKIE_REF_CODE = "evw_ref_code"        # 自家用戶分享連結用,JS 讀取拼 ?ref=XXX

# 平台白名單
PLATFORMS = ("fb", "line", "threads", "copy")

# 訪客 dedupe 窗口
VISITOR_DEDUPE_HOURS = 24

# 30s 停留視為有效
VALID_VISIT_SECONDS = 30

_REF_ALPHABET = string.ascii_uppercase + string.digits  # 36 chars


# ─────────────────────────────────────────────────────────────
# referral_code 產生器
# ─────────────────────────────────────────────────────────────

def generate_referral_code() -> str:
    """6 字元 [A-Z0-9],36^6 = 2.18B 組合,撞機率近 0。"""
    return "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))


def ensure_user_referral_code(user_id: int, session: Session | None = None) -> str:
    """如果 user 還沒有 referral_code → 產生一個並寫入。

    回傳當前 referral_code(已存在則直接回)。
    使用情境:OAuth callback、舊資料補洞、admin 修補。
    """
    def _fn(s: Session) -> str:
        user = s.scalar(select(User).where(User.id == user_id))
        if not user:
            raise ValueError(f"user {user_id} not found")
        if user.referral_code:
            return user.referral_code

        # collision retry — 6 字元 36^6 撞機率近 0,給 20 次安全網
        for _ in range(20):
            code = generate_referral_code()
            exists = s.scalar(
                select(func.count()).select_from(User).where(User.referral_code == code)
            )
            if not exists:
                user.referral_code = code
                s.flush()
                return code
        raise RuntimeError(f"failed to gen unique referral_code for user {user_id}")

    if session is not None:
        return _fn(session)
    with session_scope() as s:
        return _fn(s)


# ─────────────────────────────────────────────────────────────
# IP / UA 隱私處理
# ─────────────────────────────────────────────────────────────

def _hash_ip(ip: str | None) -> str:
    """SHA-256 + truncate 64 chars。空 IP → 'unknown' hash。

    紀律 #18 個資保護:DB 不留可識別 IP。
    """
    raw = (ip or "unknown").strip().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _truncate_ua(ua: str | None) -> str | None:
    if not ua:
        return None
    return ua[:255]


# ─────────────────────────────────────────────────────────────
# 訪客 ?ref=XXX 處理
# ─────────────────────────────────────────────────────────────

def process_ref_visit(
    ref_code: str,
    visitor_ip: str | None,
    user_agent: str | None,
) -> int | None:
    """訪客帶 ?ref=XXX 進來時呼叫。

    Returns
    -------
    int | None
        share_clicks.id(寫進 cookie 給 30s ping 用)。
        - 找不到 referral_code → None
        - referrer 自我分享(同 IP 既是 referrer 又是訪客)— 仍記錄(沒可靠去識別)
        - 24h 內同 IP 已記錄過 → 回該既有 row id(不重複插)

    紀律 #20 silent 容錯:DB 失敗 → log + 回 None,不擋 user request。
    """
    if not ref_code or len(ref_code) != 6:
        return None

    code = ref_code.strip().upper()
    ip_hash = _hash_ip(visitor_ip)
    ua = _truncate_ua(user_agent)

    try:
        with session_scope() as s:
            referrer = s.scalar(
                select(User).where(User.referral_code == code)
            )
            if not referrer:
                return None

            # 24h dedupe — 同 IP hash 對同 referrer 不重複插
            cutoff = datetime.utcnow() - timedelta(hours=VISITOR_DEDUPE_HOURS)
            existing = s.scalar(
                select(ShareClick)
                .where(ShareClick.referrer_user_id == referrer.id)
                .where(ShareClick.visitor_ip_hash == ip_hash)
                .where(ShareClick.created_at >= cutoff)
                .order_by(ShareClick.created_at.desc())
                .limit(1)
            )
            if existing:
                return existing.id

            row = ShareClick(
                referrer_user_id=referrer.id,
                visitor_ip_hash=ip_hash,
                user_agent=ua,
                is_valid=0,
            )
            s.add(row)
            s.flush()
            logger.info(
                "[share] visit recorded id=%s ref_user=%s",
                row.id, referrer.id,
            )
            return row.id
    except Exception:
        logger.exception("[share] process_ref_visit failed")
        return None


def mark_visit_valid(click_id: int, visitor_ip: str | None) -> bool:
    """30s ping endpoint 呼叫。把 share_clicks row 標 is_valid=1。

    防偽造:檢查 row.visitor_ip_hash == hash(current_ip)。
    """
    if not click_id or click_id <= 0:
        return False

    ip_hash = _hash_ip(visitor_ip)

    try:
        with session_scope() as s:
            row = s.scalar(select(ShareClick).where(ShareClick.id == click_id))
            if not row:
                return False
            if row.visitor_ip_hash != ip_hash:
                logger.info(
                    "[share] mark_valid IP mismatch click_id=%s — refuse",
                    click_id,
                )
                return False
            if row.is_valid:
                return True
            row.is_valid = 1
            s.flush()
            logger.info("[share] mark_valid click_id=%s OK", click_id)
            return True
    except Exception:
        logger.exception("[share] mark_visit_valid failed click_id=%s", click_id)
        return False


# ─────────────────────────────────────────────────────────────
# 分享按鈕點擊紀錄
# ─────────────────────────────────────────────────────────────

def record_button_click(
    user_id: int | None,
    platform: str,
    page_url: str | None,
) -> int | None:
    """用戶(或匿名)按分享按鈕 → 寫一筆紀錄。

    已登入 → 同步更新 user.last_share_at。
    """
    if platform not in PLATFORMS:
        logger.info("[share] reject unknown platform=%s", platform)
        return None

    page_url_short = (page_url or "")[:512] or None
    now = datetime.utcnow()

    try:
        with session_scope() as s:
            row = ShareButtonClick(
                user_id=user_id,
                platform=platform,
                page_url=page_url_short,
            )
            s.add(row)
            s.flush()

            if user_id:
                s.execute(
                    update(User)
                    .where(User.id == user_id)
                    .values(last_share_at=now)
                )
            return row.id
    except Exception:
        logger.exception("[share] record_button_click failed")
        return None


# ─────────────────────────────────────────────────────────────
# Admin 統計
# ─────────────────────────────────────────────────────────────

def _today_taipei_bounds() -> tuple[datetime, datetime]:
    """回 (today_start_utc_naive, tomorrow_start_utc_naive)。

    DB 都用 UTC naive datetime,Asia/Taipei 今天 00:00 = UTC 昨天 16:00。
    """
    tz = timezone(timedelta(hours=8))
    now_tw = datetime.now(tz=tz)
    start_tw = now_tw.replace(hour=0, minute=0, second=0, microsecond=0)
    end_tw = start_tw + timedelta(days=1)
    start_utc = start_tw.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_tw.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def get_admin_share_stats(top_n: int = 10) -> dict:
    """後台 /admin/analytics 用。

    回:
    - today_clicks_by_platform: {fb, line, threads, copy: count}
    - today_valid_visits: 今日有效引流數
    - today_total_visits: 今日總引流數(含 invalid)
    - top_referrers: 累計引流前 N 名 [(referral_code, display_name, total, valid)]
    - share_total_today / valid_rate
    """
    today_start, today_end = _today_taipei_bounds()

    out = {
        "today_clicks_by_platform": {p: 0 for p in PLATFORMS},
        "today_total_clicks": 0,
        "today_valid_visits": 0,
        "today_total_visits": 0,
        "top_referrers": [],
        "valid_rate_today": 0.0,
    }

    try:
        with session_scope() as s:
            # 1. 今日按平台分
            rows = s.execute(
                select(ShareButtonClick.platform, func.count())
                .where(ShareButtonClick.created_at >= today_start)
                .where(ShareButtonClick.created_at < today_end)
                .group_by(ShareButtonClick.platform)
            ).all()
            for platform, n in rows:
                if platform in out["today_clicks_by_platform"]:
                    out["today_clicks_by_platform"][platform] = int(n)
                out["today_total_clicks"] += int(n)

            # 2. 今日引流(valid / total)
            visits = s.execute(
                select(
                    func.coalesce(func.sum(ShareClick.is_valid), 0),
                    func.count(),
                )
                .where(ShareClick.created_at >= today_start)
                .where(ShareClick.created_at < today_end)
            ).first()
            if visits:
                out["today_valid_visits"] = int(visits[0] or 0)
                out["today_total_visits"] = int(visits[1] or 0)

            if out["today_total_clicks"] > 0:
                # 「轉換率」= 今日有效引流 / 今日分享數 — spec 寫法
                out["valid_rate_today"] = round(
                    100.0 * out["today_valid_visits"] / out["today_total_clicks"], 1
                )

            # 3. 累計引流前 N 名
            top_rows = s.execute(
                select(
                    User.id,
                    User.referral_code,
                    User.display_name,
                    func.count(ShareClick.id).label("total"),
                    func.coalesce(func.sum(ShareClick.is_valid), 0).label("valid"),
                )
                .join(ShareClick, ShareClick.referrer_user_id == User.id)
                .group_by(User.id)
                .order_by(func.count(ShareClick.id).desc())
                .limit(top_n)
            ).all()
            out["top_referrers"] = [
                {
                    "user_id": r[0],
                    "referral_code": r[1] or "—",
                    "display_name": r[2] or "—",
                    "total": int(r[3] or 0),
                    "valid": int(r[4] or 0),
                }
                for r in top_rows
            ]
    except Exception:
        logger.exception("[share] get_admin_share_stats failed")

    return out


# ─────────────────────────────────────────────────────────────
# AdSense 預埋 helpers — 不接到任何模板,等廣告上線再串
# ─────────────────────────────────────────────────────────────

def should_show_ad(user: dict | User | None) -> bool:
    """以後 AdSense 過審時,廣告模板用這個判斷是否顯示廣告。

    現在不接,寫好放著。

    規則:
    - user is None(未登入訪客) → True(顯示廣告)
    - user.ad_free_until 未到期 → False(免廣告)
    - 其他 → True
    """
    if not user:
        return True
    if isinstance(user, dict):
        until = user.get("ad_free_until")
    else:
        until = getattr(user, "ad_free_until", None)
    if until is None:
        return True
    if isinstance(until, str):
        try:
            until = datetime.fromisoformat(until)
        except ValueError:
            return True
    if isinstance(until, datetime) and until > datetime.utcnow():
        return False
    return True


def grant_ad_free_days(user_id: int, days: int, reason: str = "") -> datetime | None:
    """累加 ad_free_until。

    現在不呼叫,等 AdSense 上線時:
    - 用戶按分享按鈕 → grant_ad_free_days(user, 1, "share_button")
    - 訪客從 ref 連結進來且有效 → grant_ad_free_days(referrer, 7, "ref_visit_valid")

    規則:max(now, current_ad_free_until) + days,確保「當前還有的時間不被吃掉」。
    """
    if days <= 0:
        return None
    try:
        with session_scope() as s:
            user = s.scalar(select(User).where(User.id == user_id))
            if not user:
                return None
            now = datetime.utcnow()
            base = user.ad_free_until if (user.ad_free_until and user.ad_free_until > now) else now
            new_until = base + timedelta(days=days)
            user.ad_free_until = new_until
            s.flush()
            logger.info(
                "[share] grant_ad_free user=%s +%sd until=%s reason=%s",
                user_id, days, new_until.isoformat(timespec="seconds"), reason or "—",
            )
            return new_until
    except Exception:
        logger.exception("[share] grant_ad_free_days failed user_id=%s", user_id)
        return None


# ─────────────────────────────────────────────────────────────
# 工具:request → IP / UA(獨立給 router 用)
# ─────────────────────────────────────────────────────────────

def extract_visitor_ip(request) -> str | None:
    """支援 Cloudflare / Zeabur 後的 X-Forwarded-For。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    if request.client:
        return request.client.host
    return None
