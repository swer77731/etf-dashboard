"""客戶紀錄分析 — 後台 stats 計算 + TG 日報組裝 + 90 天清理。

紀律 #16:
- 時區 Asia/Taipei(stats 用「該日 00:00 Taipei 起」窗口)
- DB ts 是 UTC naive,計算前轉成 Asia/Taipei tz-aware 比對
- 90 天前資料整批 DELETE(避免 SQLite 漲檔)
- 24h 內同 IP session ≥ HIGH_SESSION_THRESHOLD(預設 15)自動從統計排除
  (不擋訪問,只 stats 排除;bot-diagnosis 仍看得到原始)
"""
from __future__ import annotations

import re
import time as _time
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select

from app.config import settings
from app.database import session_scope
from app.models.analytics import AnalyticsLog, CompareLog, SearchLog
from app.models.etf import ETF

TPE = ZoneInfo("Asia/Taipei")


# 24h 高 session IP 排除清單 — 60s memory cache 避免每張卡重查
_BOT_IP_CACHE: dict = {"ts": 0.0, "list": [], "window_hours": 24}
_BOT_IP_CACHE_TTL = 60.0


def get_high_session_ips(window_hours: int = 24) -> list[str]:
    """24h 內同 ip_masked 開 ≥ settings.high_session_threshold 個 session 的清單。

    回 ip_masked list,caller 用 NOT IN 排除。60s cache 命中後 ~0ms。
    """
    now_mono = _time.monotonic()
    if (
        _BOT_IP_CACHE["list"]
        and _BOT_IP_CACHE["window_hours"] == window_hours
        and (now_mono - _BOT_IP_CACHE["ts"]) < _BOT_IP_CACHE_TTL
    ):
        return _BOT_IP_CACHE["list"]

    cutoff = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(hours=window_hours)
    threshold = settings.high_session_threshold
    with session_scope() as s:
        rows = s.execute(
            select(AnalyticsLog.ip_masked)
            .where(AnalyticsLog.ts >= cutoff)
            .where(AnalyticsLog.ip_masked.isnot(None))
            .group_by(AnalyticsLog.ip_masked)
            .having(func.count(distinct(AnalyticsLog.session_id)) >= threshold)
        ).all()
    ips = [r.ip_masked for r in rows if r.ip_masked]
    _BOT_IP_CACHE["ts"] = now_mono
    _BOT_IP_CACHE["list"] = ips
    _BOT_IP_CACHE["window_hours"] = window_hours
    return ips


def _exclude_high_session_clause(bot_ips: list[str]):
    """回 SQLAlchemy where clause(可選),caller 用 .where(_exclude(...))"""
    if not bot_ips:
        return None
    return AnalyticsLog.ip_masked.notin_(bot_ips)


# Path → 中文功能標籤(prefix match,順序重要 — 長 prefix 先)
PATH_LABELS: list[tuple[str, str]] = [
    ("/etf/", "ETF 詳情"),
    ("/ranking/", "排行榜"),
    ("/dividend-calendar", "配息日曆"),
    ("/monthly-income", "月配試算"),
    ("/holdings", "持股分析"),
    ("/compare", "績效比較"),
    ("/news", "新聞"),
    ("/contact", "聯絡"),
    ("/changelog", "更新日誌"),
    ("/disclaimer", "免責聲明"),
    ("/terms", "使用條款"),
    ("/privacy", "隱私權"),
    ("/admin/login", "後台登入"),
    ("/", "首頁"),    # 必須最後 — 空 prefix 全匹配
]


def label_for_path(path: str) -> str:
    for prefix, label in PATH_LABELS:
        if path == "/" and prefix == "/":
            return label
        if prefix != "/" and path.startswith(prefix):
            return label
    if path == "/":
        return "首頁"
    return path


# ─────────────────────────────────────────────────────────────
# 時間窗口 helper
# ─────────────────────────────────────────────────────────────

def day_range_utc(date_taipei) -> tuple[datetime, datetime]:
    """date_taipei 是 date 物件(Taipei 該日)。回 (start_utc, end_utc) UTC naive。"""
    start_tpe = datetime(date_taipei.year, date_taipei.month, date_taipei.day, tzinfo=TPE)
    end_tpe = start_tpe + timedelta(days=1)
    return (start_tpe.astimezone(timezone.utc).replace(tzinfo=None),
            end_tpe.astimezone(timezone.utc).replace(tzinfo=None))


def today_taipei_date():
    return datetime.now(tz=TPE).date()


# ─────────────────────────────────────────────────────────────
# 主要 stats 計算
# ─────────────────────────────────────────────────────────────

def overview(target_date) -> dict:
    """單日綜合 — DAU / PV / 平均停留秒數。已排除 24h 高 session IP。"""
    start, end = day_range_utc(target_date)
    bot_ips = get_high_session_ips()
    excl = _exclude_high_session_clause(bot_ips)
    with session_scope() as s:
        dau_q = (select(func.count(distinct(AnalyticsLog.session_id)))
                 .where(AnalyticsLog.ts >= start)
                 .where(AnalyticsLog.ts < end))
        pv_q = (select(func.count(AnalyticsLog.id))
                .where(AnalyticsLog.ts >= start)
                .where(AnalyticsLog.ts < end))
        if excl is not None:
            dau_q = dau_q.where(excl)
            pv_q = pv_q.where(excl)
        dau = s.scalar(dau_q) or 0
        pv = s.scalar(pv_q) or 0
        # 平均停留 — 每 session 用 max(ts) - min(ts),只計 PV >= 2 的 session
        rows_q = (select(
                AnalyticsLog.session_id,
                func.min(AnalyticsLog.ts).label("first_ts"),
                func.max(AnalyticsLog.ts).label("last_ts"),
                func.count(AnalyticsLog.id).label("pv"),
            )
            .where(AnalyticsLog.ts >= start)
            .where(AnalyticsLog.ts < end)
            .group_by(AnalyticsLog.session_id))
        if excl is not None:
            rows_q = rows_q.where(excl)
        rows = s.execute(rows_q).all()
    multi_page = [r for r in rows if r.pv >= 2]
    if multi_page:
        avg_dur = sum((r.last_ts - r.first_ts).total_seconds() for r in multi_page) / len(multi_page)
    else:
        avg_dur = 0.0
    return {"dau": dau, "pv": pv, "avg_duration_sec": round(avg_dur, 1),
            "excluded_ip_count": len(bot_ips)}


def overview_with_diff(target_date, prev_date) -> dict:
    """target vs prev 含 % 變化。"""
    today = overview(target_date)
    prev = overview(prev_date)

    def pct(now, before):
        if before == 0:
            return None
        return round((now - before) / before * 100, 1)

    today["dau_change_pct"] = pct(today["dau"], prev["dau"])
    today["pv_change_pct"] = pct(today["pv"], prev["pv"])
    today["dur_change_pct"] = pct(today["avg_duration_sec"], prev["avg_duration_sec"])
    today["prev_dau"] = prev["dau"]
    today["prev_pv"] = prev["pv"]
    return today


def dau_trend(days: int = 30) -> list[dict]:
    """近 N 天每日 DAU + PV。已排除 24h 高 session IP。"""
    today = today_taipei_date()
    bot_ips = get_high_session_ips()
    excl = _exclude_high_session_clause(bot_ips)
    out = []
    with session_scope() as s:
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            start, end = day_range_utc(d)
            dau_q = (select(func.count(distinct(AnalyticsLog.session_id)))
                     .where(AnalyticsLog.ts >= start)
                     .where(AnalyticsLog.ts < end))
            pv_q = (select(func.count(AnalyticsLog.id))
                    .where(AnalyticsLog.ts >= start)
                    .where(AnalyticsLog.ts < end))
            if excl is not None:
                dau_q = dau_q.where(excl)
                pv_q = pv_q.where(excl)
            dau = s.scalar(dau_q) or 0
            pv = s.scalar(pv_q) or 0
            out.append({"date": d.isoformat(), "dau": dau, "pv": pv})
    return out


def top_etfs(days: int = 7, limit: int = 10) -> list[dict]:
    """熱門 ETF — 從 /etf/{code} 路徑統計。已排除 24h 高 session IP。"""
    today = today_taipei_date()
    start, _ = day_range_utc(today - timedelta(days=days - 1))
    end, _ = day_range_utc(today + timedelta(days=1))
    pattern = re.compile(r"^/etf/([A-Za-z0-9]+)$")
    excl = _exclude_high_session_clause(get_high_session_ips())
    with session_scope() as s:
        q = (select(AnalyticsLog.path, func.count(AnalyticsLog.id).label("cnt"))
            .where(AnalyticsLog.ts >= start)
            .where(AnalyticsLog.ts < end)
            .where(AnalyticsLog.path.like("/etf/%"))
            .group_by(AnalyticsLog.path))
        if excl is not None:
            q = q.where(excl)
        rows = s.execute(q).all()
        # 從路徑解出 code
        codes_count = Counter()
        for r in rows:
            m = pattern.match(r.path)
            if m:
                codes_count[m.group(1).upper()] += r.cnt
        if not codes_count:
            return []
        # 撈 ETF 名字
        top_codes = [c for c, _ in codes_count.most_common(limit)]
        etfs = s.scalars(select(ETF).where(ETF.code.in_(top_codes))).all()
        name_map = {e.code: e.name for e in etfs}
    return [
        {"code": c, "name": name_map.get(c, ""), "count": codes_count[c]}
        for c, _ in codes_count.most_common(limit)
    ]


def top_features(days: int = 7, limit: int = 10) -> list[dict]:
    """熱門功能 — path 用 label_for_path 對應後再聚合。已排除 24h 高 session IP。"""
    today = today_taipei_date()
    start, _ = day_range_utc(today - timedelta(days=days - 1))
    end, _ = day_range_utc(today + timedelta(days=1))
    excl = _exclude_high_session_clause(get_high_session_ips())
    counter = Counter()
    with session_scope() as s:
        q = (select(AnalyticsLog.path, func.count(AnalyticsLog.id).label("cnt"))
            .where(AnalyticsLog.ts >= start)
            .where(AnalyticsLog.ts < end)
            .group_by(AnalyticsLog.path))
        if excl is not None:
            q = q.where(excl)
        rows = s.execute(q).all()
    for r in rows:
        counter[label_for_path(r.path)] += r.cnt
    return [{"label": k, "count": v} for k, v in counter.most_common(limit)]


def top_searches(days: int = 7, limit: int = 20) -> list[dict]:
    """熱門搜尋關鍵字。"""
    today = today_taipei_date()
    start, _ = day_range_utc(today - timedelta(days=days - 1))
    end, _ = day_range_utc(today + timedelta(days=1))
    with session_scope() as s:
        rows = s.execute(
            select(SearchLog.q, func.count(SearchLog.id).label("cnt"))
            .where(SearchLog.ts >= start)
            .where(SearchLog.ts < end)
            .group_by(SearchLog.q)
            .order_by(func.count(SearchLog.id).desc())
            .limit(limit)
        ).all()
    return [{"q": r.q, "count": r.cnt} for r in rows]


def top_compares(days: int = 7, limit: int = 10) -> list[dict]:
    """熱門比較組合(codes 已排序)。"""
    today = today_taipei_date()
    start, _ = day_range_utc(today - timedelta(days=days - 1))
    end, _ = day_range_utc(today + timedelta(days=1))
    with session_scope() as s:
        rows = s.execute(
            select(CompareLog.codes_sorted, func.count(CompareLog.id).label("cnt"))
            .where(CompareLog.ts >= start)
            .where(CompareLog.ts < end)
            .group_by(CompareLog.codes_sorted)
            .order_by(func.count(CompareLog.id).desc())
            .limit(limit)
        ).all()
    return [{"codes": r.codes_sorted, "count": r.cnt} for r in rows]


# ─────────────────────────────────────────────────────────────
# 流量來源解析
# ─────────────────────────────────────────────────────────────

_INTERNAL_HOSTS = ("swer-etf.zeabur.app", "127.0.0.1", "localhost")


def _classify_referer(ref: str | None) -> str:
    if not ref:
        return "直接"
    try:
        host = urlparse(ref).hostname or ""
    except Exception:
        return "其他"
    host = host.lower()
    if any(host == h or host.endswith("." + h) for h in _INTERNAL_HOSTS):
        return "站內"
    if "google" in host:
        return "Google"
    if "facebook.com" in host or "fb.com" in host:
        return "FB"
    if "instagram.com" in host:
        return "IG"
    if "ptt.cc" in host or "ptt.com" in host:
        return "PTT"
    if "twitter.com" in host or "x.com" in host:
        return "X / Twitter"
    if "yahoo" in host:
        return "Yahoo"
    if "bing.com" in host:
        return "Bing"
    if "dcard.tw" in host:
        return "Dcard"
    return host or "其他"


def referer_breakdown(days: int = 7) -> list[dict]:
    """流量來源占比。'站內' 不算對外來源,從統計裡剔除。已排除 24h 高 session IP。"""
    today = today_taipei_date()
    start, _ = day_range_utc(today - timedelta(days=days - 1))
    end, _ = day_range_utc(today + timedelta(days=1))
    excl = _exclude_high_session_clause(get_high_session_ips())
    with session_scope() as s:
        q = (select(AnalyticsLog.referer, func.count(AnalyticsLog.id).label("cnt"))
            .where(AnalyticsLog.ts >= start)
            .where(AnalyticsLog.ts < end)
            .group_by(AnalyticsLog.referer))
        if excl is not None:
            q = q.where(excl)
        rows = s.execute(q).all()
    counter = Counter()
    for r in rows:
        cls = _classify_referer(r.referer)
        if cls == "站內":
            continue
        counter[cls] += r.cnt
    total = sum(counter.values()) or 1
    return [
        {"label": k, "count": v, "pct": round(v / total * 100, 1)}
        for k, v in counter.most_common()
    ]


# ─────────────────────────────────────────────────────────────
# UA browser + OS 解析(規則式 — 不引第三方,夠 95% case)
# ─────────────────────────────────────────────────────────────

def _parse_ua(ua: str | None) -> str:
    """從 UA 字串截 'Browser / OS'。看不出來回 '其他'。"""
    if not ua:
        return "—"
    u = ua

    # OS 偵測(順序:具體→泛)
    if "iPhone" in u or "iPad" in u or "iPod" in u:
        os_name = "iOS"
    elif "Android" in u:
        os_name = "Android"
    elif "Mac OS X" in u or "Macintosh" in u:
        os_name = "macOS"
    elif "Windows NT 10.0" in u or "Windows NT 11" in u:
        os_name = "Windows"
    elif "Windows" in u:
        os_name = "Windows"
    elif "Linux" in u:
        os_name = "Linux"
    elif "CrOS" in u:
        os_name = "ChromeOS"
    else:
        os_name = "其他"

    # Browser 偵測(順序重要 — Chrome 含 Safari 字樣,要先擋)
    if "Line/" in u or "Line " in u:
        br = "LINE"
    elif "FBAN/" in u or "FBAV/" in u:
        br = "FB App"
    elif "Instagram" in u:
        br = "IG App"
    elif "Edg/" in u:
        br = "Edge"
    elif "OPR/" in u or "Opera" in u:
        br = "Opera"
    elif "SamsungBrowser" in u:
        br = "Samsung"
    elif "Firefox/" in u:
        br = "Firefox"
    elif "Chrome/" in u:
        # Edge / Opera / Samsung 已先擋掉,剩下 Chrome/ 都算 Chrome
        br = "Chrome"
    elif "Safari/" in u and "Version/" in u:
        br = "Safari"
    elif "bot" in u.lower() or "crawl" in u.lower() or "spider" in u.lower():
        br = "Bot"
    else:
        br = "其他"

    return f"{br} / {os_name}"


# ─────────────────────────────────────────────────────────────
# 最近 100 筆即時表格
# ─────────────────────────────────────────────────────────────

def recent_visits(limit: int = 100) -> list[dict]:
    """最新 N 筆訪問(IP 已遮、UA 取 browser+OS)。"""
    with session_scope() as s:
        rows = s.scalars(
            select(AnalyticsLog).order_by(AnalyticsLog.id.desc()).limit(limit)
        ).all()
        return [
            {
                "id": r.id,
                "ts_utc": r.ts.isoformat() if r.ts else None,
                "session_id_short": (r.session_id or "")[:8],
                "ip_masked": r.ip_masked,
                "path": r.path,
                "label": label_for_path(r.path),
                "query_string": r.query_string,
                "referer": r.referer,
                "ua_short": _parse_ua(r.ua),
                "duration_sec": r.duration_sec,
            }
            for r in rows
        ]


# ─────────────────────────────────────────────────────────────
# Daily TG 日報組裝
# ─────────────────────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)} 秒"
    m, s = divmod(int(seconds), 60)
    return f"{m} 分 {s:02d} 秒"


def build_daily_report(target_date=None) -> str:
    """組今日 TG 日報文字。target_date 預設今天(Taipei)。

    無流量時回 fallback 字串。
    """
    target_date = target_date or today_taipei_date()
    ov = overview(target_date)
    if ov["dau"] == 0 and ov["pv"] == 0:
        return f"📊 ETF 觀察室 · {target_date.isoformat()} 今日尚無流量,明天再見"

    etfs = top_etfs(days=1, limit=2)
    feats = top_features(days=1, limit=2)
    searches = top_searches(days=1, limit=1)
    compares = top_compares(days=1, limit=1)
    refs = referer_breakdown(days=1)

    lines = [f"📊 ETF 觀察室 · {target_date.isoformat()}"]
    lines.append(
        f"【流量】訪客 {ov['dau']} 人 / 瀏覽 {ov['pv']} 頁 / "
        f"平均停留 {_fmt_duration(ov['avg_duration_sec'])}"
    )
    if etfs:
        parts = [f"{i+1}. {e['code']}({e['count']} 次)" for i, e in enumerate(etfs)]
        lines.append("【熱門 ETF】" + " ".join(parts))
    if feats:
        parts = [f"{i+1}. {f['label']}({f['count']} 次)" for i, f in enumerate(feats)]
        lines.append("【熱門功能】" + " ".join(parts))
    if searches:
        parts = [f"{s['q']}({s['count']} 次)" for s in searches]
        lines.append("【熱門搜尋】" + " ".join(parts))
    if compares:
        parts = [
            f"{i+1}. {c['codes'].replace(',', '+')}({c['count']} 次)"
            for i, c in enumerate(compares)
        ]
        lines.append("【熱門比較組合】" + " ".join(parts))
    if refs:
        # 取前 4 個來源,湊百分比
        top4 = refs[:4]
        parts = [f"{r['label']} {r['pct']:.0f}%" for r in top4]
        lines.append("【流量來源】" + " / ".join(parts))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 90 天清理
# ─────────────────────────────────────────────────────────────

def cleanup_old_logs(retain_days: int = 90) -> dict:
    """刪 retain_days 之前的所有紀錄(三張表)。回 stats。"""
    cutoff_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(days=retain_days)
    deleted = {"analytics_log": 0, "search_log": 0, "compare_log": 0}
    with session_scope() as s:
        from sqlalchemy import delete
        for tbl, model in (
            ("analytics_log", AnalyticsLog),
            ("search_log", SearchLog),
            ("compare_log", CompareLog),
        ):
            r = s.execute(delete(model).where(model.ts < cutoff_utc))
            deleted[tbl] = r.rowcount or 0
    return {"cutoff_utc": cutoff_utc.isoformat(), **deleted}
