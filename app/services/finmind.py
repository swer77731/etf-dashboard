"""FinMind API wrapper — 唯一一處直接打 FinMind 的地方。

核心紀律(寫在 CLAUDE.md「FinMind API 配額禮讓」):
- token 與其他人共用,單小時用量必留 ≥ 50% 給其他人
- 任何外部呼叫只能走本檔的 `request()`,不准散落在各處用 httpx
- 接近紅線(≥ 45%)主動暫停,sleep 到下個整點
- 全程 throttle:每筆呼叫之間至少間隔 `MIN_INTERVAL_SEC`
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# 紀律 #18:過濾敏感欄位防 log / error message 洩漏
# 樣本:?token=eyJ0... → ?token=[REDACTED]
_SECRET_RE = re.compile(
    r"(token|password|api[_-]?key|secret|authorization)=([^&\s]+)",
    re.IGNORECASE,
)


def _redact(s: str) -> str:
    """打碼 URL / 字串裡的 token / password 等敏感欄位。"""
    if not s:
        return s
    return _SECRET_RE.sub(r"\1=[REDACTED]", str(s))

# === 常數 ===
DATA_URL = "https://api.finmindtrade.com/api/v4/data"
USER_INFO_URL = "https://api.web.finmindtrade.com/v2/user_info"

# 配額禮讓:單小時用量超過 SAFE_RATIO 就主動退讓
SAFE_RATIO = 0.45        # 預警紅線(寬鬆,還沒到 50% 就退)
HARD_RATIO = 0.50        # 絕對紅線

# Throttle:每次呼叫之間最少間隔(秒),避免短時間 burst
MIN_INTERVAL_SEC = 1.0

# Quota 查詢快取(避免每次呼叫都查一次)
_QUOTA_CACHE_TTL_SEC = 30
_quota_cache: dict = {"ts": 0.0, "value": None}
_throttle_last_call_ts = 0.0
_lock = threading.Lock()


# === 公開資料結構 ===
@dataclass(frozen=True, slots=True)
class Quota:
    used: int           # 本小時已使用次數
    limit_hour: int     # 本小時上限
    ratio: float        # used / limit_hour
    room: int           # 我們還能用幾次(扣掉 50% 禮讓後)
    level: str          # Sponsor / Backer / etc.

    @property
    def near_red_line(self) -> bool:
        return self.ratio >= SAFE_RATIO

    @property
    def over_red_line(self) -> bool:
        return self.ratio >= HARD_RATIO


class FinMindError(RuntimeError):
    pass


class FinMindQuotaExceeded(FinMindError):
    pass


# === API ===
def check_quota(force: bool = False) -> Quota:
    """打 /v2/user_info 查當前配額。30 秒內快取避免重複呼叫。"""
    now = time.time()
    if not force and _quota_cache["value"] and (now - _quota_cache["ts"] < _QUOTA_CACHE_TTL_SEC):
        return _quota_cache["value"]

    token = settings.finmind_api_token or ""
    r = httpx.get(
        USER_INFO_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"token": token},
        timeout=10,
    )
    r.raise_for_status()
    payload = r.json()

    used = int(payload.get("user_count", 0))
    limit = int(payload.get("api_request_limit_hour", 0))
    level = payload.get("level_title", "unknown")

    ratio = used / limit if limit > 0 else 1.0
    half = limit // 2
    room = max(0, half - used)

    quota = Quota(used=used, limit_hour=limit, ratio=ratio, room=room, level=level)
    _quota_cache["ts"] = now
    _quota_cache["value"] = quota
    return quota


def _throttle() -> None:
    """確保每次呼叫之間至少間隔 MIN_INTERVAL_SEC。"""
    global _throttle_last_call_ts
    with _lock:
        elapsed = time.time() - _throttle_last_call_ts
        if elapsed < MIN_INTERVAL_SEC:
            time.sleep(MIN_INTERVAL_SEC - elapsed)
        _throttle_last_call_ts = time.time()


def _seconds_until_next_hour() -> int:
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
    return int((nxt - now).total_seconds())


def _wait_for_quota_reset(reason: str) -> None:
    secs = _seconds_until_next_hour()
    logger.warning(
        "[finmind] quota guard: %s — sleeping %d sec until next hour",
        reason, secs,
    )
    time.sleep(secs)
    check_quota(force=True)  # 重新查


def request(
    dataset: str,
    *,
    data_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    extra_params: dict | None = None,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> list[dict]:
    """打 FinMind /api/v4/data,內建 throttle + quota 檢查 + 自動退讓。

    回傳 dataset 的 `data` list(空 list 也是合法回傳)。

    配額守門兩層:
      1. 自家 33% local quota(finmind_quota_log,過去 60 分鐘 < 2000)
         超過 → sleep 到下個整點 + 重試一次,仍超 → raise FinMindQuotaExhausted
      2. FinMind GLOBAL ratio(/user_info,含朋友)— 既有 HARD_RATIO 50%
         超過 → sleep 到下個整點(safety net)
    成功打到 FinMind → record_finmind_call 寫 finmind_quota_log。
    """
    # 延遲 import 避免循環
    from app.services.finmind_quota import (
        FinMindQuotaExhausted,
        check_finmind_quota,
        log_quota_status_to_sync_status,
        record_finmind_call,
        should_block,
    )

    token = settings.finmind_api_token or ""
    params: dict = {"dataset": dataset, "token": token}
    if data_id is not None:
        params["data_id"] = data_id
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    if extra_params:
        params.update(extra_params)

    # === 自家 quota gate(2000/hr 自家額度,不含朋友)===
    block, _mins = should_block()
    if block:
        secs = _seconds_until_next_hour() + 30   # 緩衝 30 秒
        logger.warning(
            "[finmind] LOCAL quota exhausted in last 60min, "
            "sleep %d sec until next hour", secs,
        )
        time.sleep(secs)
        # 醒來再驗證一次,仍超 → raise(讓 caller 標 partial)
        if not check_finmind_quota():
            raise FinMindQuotaExhausted(
                "FinMind local quota still exhausted after sleep"
            )

    # === FinMind GLOBAL ratio(safety net,含朋友)===
    quota = check_quota()
    if quota.over_red_line:
        _wait_for_quota_reset(f"used {quota.used}/{quota.limit_hour} ({quota.ratio:.1%}) >= {HARD_RATIO:.0%}")

    _throttle()

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            r = httpx.get(DATA_URL, params=params, timeout=timeout)
            if r.status_code == 402:
                # FinMind 文件:402 = quota exceeded
                logger.error("[finmind] 402 quota exceeded — going to sleep")
                _wait_for_quota_reset("API returned 402")
                continue
            r.raise_for_status()
            payload = r.json()
            if payload.get("status") not in (None, 200):
                raise FinMindError(f"FinMind error: {payload.get('msg')!r} (status={payload.get('status')})")
            # === 自家用量計數(成功才記)===
            try:
                record_finmind_call(dataset)
                log_quota_status_to_sync_status()
            except Exception:
                logger.exception("[finmind] record/log quota failed (non-fatal)")
            return payload.get("data", []) or []
        except httpx.HTTPError as e:
            last_err = e
            wait = 2 ** attempt
            # 紀律 #18:redact token 才能進 log
            logger.warning("[finmind] http error attempt %d/%d: %s — retry in %ds",
                          attempt, max_retries, _redact(str(e)), wait)
            time.sleep(wait)

    # 紀律 #18:raise 出去的 message 也要 redact
    raise FinMindError(
        f"FinMind request failed after {max_retries} retries: {_redact(str(last_err))}"
    )


def log_quota(prefix: str = "") -> Quota:
    q = check_quota(force=True)
    logger.info(
        "[finmind][quota] %s used=%d/%d (%.1f%%) room=%d level=%s",
        prefix, q.used, q.limit_hour, q.ratio * 100, q.room, q.level,
    )
    return q
