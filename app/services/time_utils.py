"""News / publish time formatting helpers — server-side relative time。

紀律 #1「白癡都看得懂」+ 紀律 #19「字級基準」:
排行榜 / 新聞牆顯示「N 分鐘前 / N 小時前 / N 天前 / YYYY-MM-DD」,
散戶一眼分得出新舊,不必腦中換算。

時區一律用 settings.scheduler_timezone(預設 Asia/Taipei)— FinMind
TaiwanStockNews 回的 "YYYY-MM-DD HH:MM:SS" 本來就是台灣時間 naive,
這層把它當成 Taipei 來比,server 跑在 UTC 也不會錯。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

_TZ = ZoneInfo(settings.scheduler_timezone)


def _parse_to_taipei(value) -> datetime | None:
    """Parse ISO 字串或 datetime → Asia/Taipei tz-aware datetime。

    支援:
    - datetime(naive 視為 Taipei,aware 自動 astimezone)
    - "2026-04-29T10:30:00"(FinMind serialize 出來的格式)
    - "2026-04-29 10:30:00"
    - "2026-04-29T10:30:00Z"(若未來有外部 UTC 來源)
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace(" ", "T")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ)


def humanize_relative(value) -> str:
    """ISO / datetime → 「剛剛 / N 分鐘前 / N 小時前 / N 天前 / YYYY-MM-DD」。

    規則(紀律 #16 — 統一規格,server-side render,JS 失效也對):
    - < 1 分     → 剛剛
    - < 1 小時   → N 分鐘前
    - < 24 小時  → N 小時前
    - < 7 天     → N 天前
    - >= 7 天    → YYYY-MM-DD(原始日期,讓使用者知道大致時間)
    """
    dt = _parse_to_taipei(value)
    if dt is None:
        return ""
    now = datetime.now(tz=_TZ)
    diff_sec = (now - dt).total_seconds()
    if diff_sec < 0:
        # 未來時間(排程預告 / 時鐘漂移),保守 fallback 到日期
        return dt.strftime("%Y-%m-%d")
    if diff_sec < 60:
        return "剛剛"
    if diff_sec < 3600:
        return f"{int(diff_sec // 60)} 分鐘前"
    if diff_sec < 86400:
        return f"{int(diff_sec // 3600)} 小時前"
    if diff_sec < 86400 * 7:
        return f"{int(diff_sec // 86400)} 天前"
    return dt.strftime("%Y-%m-%d")


def is_fresh_news(value, hours: int = 6) -> bool:
    """< N 小時 = fresh(UI 顯紅色強調)。預設 6 小時。"""
    dt = _parse_to_taipei(value)
    if dt is None:
        return False
    now = datetime.now(tz=_TZ)
    diff_sec = (now - dt).total_seconds()
    return 0 <= diff_sec < hours * 3600
