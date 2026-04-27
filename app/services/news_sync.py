"""新聞同步:從 FinMind TaiwanStockNews 抓主流 ETF 相關新聞,寫入 news table。

策略:
- 主流 3 類(主動 + 市值 + 高股息)~55 支 ETF
- 每天 14:30 抓最近 1~2 天的新聞(已 URL 去重)
- 自動標記 etf_tags(該則新聞 link 對應的 ETF 代號清單,可疊加多支)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import session_scope
from app.models.etf import ETF
from app.models.news import News
from app.services import finmind

logger = logging.getLogger(__name__)


# 預設關注類別 — 主流 3 類(避免槓桿/反向/債券新聞污染主流量)
DEFAULT_NEWS_CATEGORIES = ("active", "market", "dividend")


# FinMind TaiwanStockNews 實際 schema(2026-04-27 驗證):
#   { date: "YYYY-MM-DD HH:MM:SS", stock_id, link, source, title }
# 但欄位名 / 格式未來可能變,寫成容錯版本。
_NEWS_DATE_KEYS = ("date", "publish_time", "time", "publishedAt", "published_at")
_NEWS_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def _parse_news_datetime(row: dict) -> datetime | None:
    """從 FinMind row 拿日期 — 多欄位名 + 多格式容錯。

    解析失敗會 log.warning(欄位 keys + 嘗試的字串),讓未來 schema 改變立刻看見。
    """
    raw = None
    used_key = None
    for key in _NEWS_DATE_KEYS:
        v = row.get(key)
        if v:
            raw = str(v).strip()
            used_key = key
            break
    if not raw:
        return None

    # 切到最大可能長度(YYYY-MM-DDTHH:MM:SS = 19 chars)
    s = raw[:19]
    for fmt in _NEWS_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue

    logger.warning(
        "[news_sync] cannot parse date %r (key=%s, available_keys=%s)",
        raw, used_key, list(row.keys()),
    )
    return None


def list_news_target_codes(categories: Iterable[str] = DEFAULT_NEWS_CATEGORIES) -> list[str]:
    """挑要抓新聞的 ETF 代號集 — 預設主流 3 類。"""
    with session_scope() as session:
        rows = session.scalars(
            select(ETF.code)
            .where(ETF.is_active.is_(True))
            .where(ETF.category.in_(list(categories)))
            .order_by(ETF.code.asc())
        ).all()
    return list(rows)


def _fetch_one(code: str, day: date) -> list[dict]:
    """單一 ETF 單日新聞 — TaiwanStockNews 不接受 end_date,只給 start_date。"""
    return finmind.request(
        "TaiwanStockNews",
        data_id=code,
        start_date=day.strftime("%Y-%m-%d"),
    )


def _persist_news(rows: list[dict], etf_code: str) -> int:
    """寫入 news table — URL 為 unique key,衝突時把 etf_code 加進 etf_tags JSON。

    防呆:
    - 同批 FinMind 回傳常含 dup URL → 先用 dict 去重(後者覆蓋前者)
    - 跨 session 已存在的 URL → 走 update tags 路徑
    """
    if not rows:
        return 0

    # 1. Batch 內 URL 去重
    by_url: dict[str, dict] = {}
    for r in rows:
        url = (r.get("link") or "").strip()
        title = (r.get("title") or "").strip()
        if not url or not title:
            continue
        by_url[url] = r   # 同 URL 後者覆蓋
    if not by_url:
        return 0

    written = 0
    with session_scope() as session:
        # 2. 一次撈出 DB 已有的 URL,後續判斷快速
        urls = list(by_url.keys())
        existing_map = {
            n.url: n
            for n in session.scalars(select(News).where(News.url.in_(urls))).all()
        }

        for url, r in by_url.items():
            title = (r.get("title") or "").strip()
            source = (r.get("source") or "").strip()
            pub_dt = _parse_news_datetime(r)

            existing = existing_map.get(url)
            if existing:
                tags = list(existing.etf_tags or [])
                if etf_code not in tags:
                    tags.append(etf_code)
                    existing.etf_tags = tags
                # 補 NULL published_at(舊 row parser bug 留下的壞資料)
                # 只在「現有為 NULL 且新值可解析」時補,有值不覆寫
                if existing.published_at is None and pub_dt is not None:
                    existing.published_at = pub_dt
                continue

            session.add(News(
                title=title[:1000],
                url=url[:512],
                source=source[:64] if source else None,
                published_at=pub_dt,
                etf_tags=[etf_code],
            ))
            written += 1
    return written


def sync_recent(days_back: int = 2, codes: Iterable[str] | None = None) -> dict:
    """每日排程入口 — 為指定 ETF 抓最近 N 天新聞,寫入 DB。

    days_back=2 涵蓋週末(週一啟動可補週六週日)
    """
    finmind.log_quota("before news sync")

    code_list = list(codes) if codes is not None else list_news_target_codes()
    today = date.today()
    days = [today - timedelta(days=i) for i in range(days_back)]

    summary = {
        "etfs": len(code_list),
        "days": len(days),
        "calls": 0,
        "rows_written": 0,
        "rows_seen": 0,
        "errors": 0,
    }

    for i, code in enumerate(code_list, start=1):
        for d in days:
            try:
                rows = _fetch_one(code, d)
                summary["calls"] += 1
                summary["rows_seen"] += len(rows)
                summary["rows_written"] += _persist_news(rows, code)
            except Exception as e:
                summary["errors"] += 1
                logger.exception("[news_sync] failed %s @ %s: %s", code, d, e)
        if i % 10 == 0 or i == len(code_list):
            q = finmind.check_quota()
            logger.info(
                "[news_sync] progress %d/%d | calls=%d written=%d | quota=%d/%d (%.1f%%)",
                i, len(code_list), summary["calls"], summary["rows_written"],
                q.used, q.limit_hour, q.ratio * 100,
            )

    finmind.log_quota("after news sync")
    logger.info("[news_sync] done: %s", summary)
    return summary


def list_recent_news(
    *,
    etf_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
    days: int | None = None,
) -> list[dict]:
    """讀本地 news table — 給 router 用。

    Args:
        etf_code: 可選,只看與此 ETF 相關
        limit / offset: 分頁
        days: 可選,只看「近 N 天」(由 published_at 計算);None = 全部
    """
    with session_scope() as session:
        stmt = select(News).order_by(News.published_at.desc().nullslast(), News.id.desc())
        if etf_code:
            etf_code = etf_code.upper()
            # SQLite JSON contains check
            stmt = stmt.where(func.instr(func.json(News.etf_tags), f'"{etf_code}"') > 0)
        if days is not None and days > 0:
            since = datetime.now() - timedelta(days=days)
            stmt = stmt.where(News.published_at >= since)
        rows = session.scalars(stmt.offset(offset).limit(limit)).all()
        out = [{
            "id": n.id,
            "title": n.title,
            "url": n.url,
            "source": n.source,
            "published_at": n.published_at.isoformat() if n.published_at else None,
            "etf_tags": list(n.etf_tags or []),
        } for n in rows]
    return out


def count_news(etf_code: str | None = None, days: int | None = None) -> int:
    with session_scope() as session:
        stmt = select(func.count(News.id))
        if etf_code:
            etf_code = etf_code.upper()
            stmt = stmt.where(func.instr(func.json(News.etf_tags), f'"{etf_code}"') > 0)
        if days is not None and days > 0:
            since = datetime.now() - timedelta(days=days)
            stmt = stmt.where(News.published_at >= since)
        return session.scalar(stmt) or 0
