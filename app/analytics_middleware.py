"""客戶紀錄分析 — Middleware 攔每個 GET 寫紀錄。

紀律 #16:
- IP 一律遮罩(末段 xxx),不存原始 IP
- session_id = 32-hex HttpOnly cookie 7 天
- 同 session 同 path 5 秒去重(防 reload spam)
- 排除清單跳過(/static、/admin/*、/api/etf/search、/healthz、/favicon.ico)
- /api/etf/search 排除主表 + 不寫 search_log(由 search endpoint 自寫,
  能拿到 hits 命中筆數)
- /compare 主表 + 寫 compare_log(codes 排序版)
- 寫 DB 失敗只 log warning,不擋 user request
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.database import session_scope
from app.models.analytics import AnalyticsLog, CompareLog

logger = logging.getLogger(__name__)

COOKIE_NAME = "etfw_sid"
COOKIE_MAX_AGE = 7 * 24 * 3600   # 7 天

# 排除清單(prefix match)— analytics_log 不收
_EXCLUDE_PREFIXES = ("/static", "/admin/", "/api/etf/search")
_EXCLUDE_EXACT = (
    "/healthz", "/health", "/api/health",
    "/favicon.ico", "/robots.txt",
    "/admin",   # /admin 本身也跳
)

DEDUP_SECONDS = 5


def _ip_mask(ip: str | None) -> str | None:
    """末段遮 xxx — IPv4: 124.156.222.63 → 124.156.222.xxx
                    IPv6: 前 4 hextets + ::xxx
    """
    if not ip:
        return None
    ip = ip.strip()
    if "." in ip and ip.count(".") == 3:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.xxx"
    if ":" in ip:
        parts = ip.split(":")
        if len(parts) >= 4:
            return ":".join(parts[:4]) + "::xxx"
    return ip


def _client_ip(request: Request) -> str | None:
    """Zeabur 反向代理 → X-Forwarded-For: client, proxy1, proxy2"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _get_or_create_sid(request: Request) -> tuple[str, bool]:
    """Return (sid, was_new)。"""
    sid = request.cookies.get(COOKIE_NAME)
    # 32 hex 才接受(防 user 自塞髒值)
    if sid and len(sid) == 32 and all(c in "0123456789abcdef" for c in sid):
        return sid, False
    return secrets.token_hex(16), True


def _should_skip(path: str) -> bool:
    if path in _EXCLUDE_EXACT:
        return True
    for prefix in _EXCLUDE_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


# Module-level dedup cache
_DEDUP_CACHE: dict[tuple[str, str], float] = {}
_DEDUP_MAX_ENTRIES = 10000


def _is_duplicate(sid: str, path: str) -> bool:
    """同 sid 同 path,5 秒內第 2 次以上 = 重複,不寫。"""
    now = time.monotonic()
    key = (sid, path)
    last = _DEDUP_CACHE.get(key)
    if last is not None and (now - last) < DEDUP_SECONDS:
        _DEDUP_CACHE[key] = now
        return True
    _DEDUP_CACHE[key] = now
    if len(_DEDUP_CACHE) > _DEDUP_MAX_ENTRIES:
        cutoff = now - DEDUP_SECONDS * 2
        for k in [k for k, t in _DEDUP_CACHE.items() if t < cutoff]:
            _DEDUP_CACHE.pop(k, None)
    return False


def _now_utc_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class AnalyticsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 只追 GET
        if request.method != "GET":
            return await call_next(request)

        path = request.url.path
        sid, sid_new = _get_or_create_sid(request)
        # 把 sid 塞進 request.state 給 router 用(將來會員系統綁 user_id 用得到)
        request.state.session_id = sid

        t0 = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - t0

        if sid_new:
            response.set_cookie(
                COOKIE_NAME, sid,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=False,   # Zeabur edge 已 HTTPS,內部 HTTP 也沒差(同 host)
            )

        # 寫 log — 失敗不影響 response
        try:
            self._log(request, sid, duration, path, response.status_code)
        except (SQLAlchemyError, Exception) as e:
            logger.warning("[analytics] log failed: %s", e)

        return response

    def _log(self, request: Request, sid: str, duration: float, path: str, status: int) -> None:
        # 4xx / 5xx 不算流量(404 page 不該進 hot list)
        if status >= 400:
            return

        now = _now_utc_naive()

        # /api/etf/search:排除清單,middleware 不寫 search_log
        # search_log 由 endpoint 自寫,能拿到 hits 命中筆數
        if path.startswith("/api/etf/search"):
            return

        # /compare:主表 + compare_log(codes_sorted)
        if path == "/compare":
            codes_raw = (request.query_params.get("codes") or "").strip()
            if codes_raw:
                code_list = sorted({c.strip().upper() for c in codes_raw.split(",") if c.strip()})[:6]
                if code_list:
                    with session_scope() as s:
                        s.add(CompareLog(codes_sorted=",".join(code_list), ts=now))

        # 主表 — 跳排除清單
        if _should_skip(path):
            return

        # 5 秒去重
        if _is_duplicate(sid, path):
            return

        ip = _client_ip(request)
        ua = (request.headers.get("user-agent") or "")[:512]
        referer = (request.headers.get("referer") or "").strip()[:512] or None
        qs = str(request.url.query)[:1024] if request.url.query else None

        with session_scope() as s:
            s.add(AnalyticsLog(
                session_id=sid,
                user_id=None,
                ip_masked=_ip_mask(ip),
                ua=ua,
                path=path[:512],
                query_string=qs,
                referer=referer,
                ts=now,
                duration_sec=round(duration, 4),
            ))
