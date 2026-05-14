"""Migration 021:清掉 referrals 表內 bot UA 的歷史紀錄。

起源:2026-05-14 user 發現 /admin/referrals 顯示「14 次推薦」,
但點開看 11+ 筆是 `facebookexternalhit` — 用戶把連結分享到 FB 後,
FB 預覽爬蟲一次拉 URL 3-12 次,**全部被當成「訪客點擊」計分**。

修法:
1. paywall.grant_trial_check 入口加 bot UA 過濾(不再記新的)— 程式層
2. 本 migration 清掉歷史已寫入的 bot rows,並重算 user_plans.total_share_count

冪等:重跑只刪「還在的 bot rows」+ 重算 count。
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import inspect, text

from app.database import engine

logger = logging.getLogger(__name__)


# 與 paywall._BOT_UA_RE 同步(SQLite LIKE 沒 regex,用 OR 列舉常見關鍵字)
BOT_KEYWORDS = [
    "facebookexternalhit", "FacebookBot", "meta-externalagent",
    "Twitterbot", "LinkedInBot", "Slackbot", "TelegramBot", "WhatsApp", "Discordbot",
    "LineBotWebHook", "ThreadsFetcher", "ThreadsExternalFetcher",
    "Googlebot", "bingbot", "Baiduspider", "YandexBot", "DuckDuckBot", "Applebot",
    "AhrefsBot", "SemrushBot", "MJ12bot", "DotBot", "PetalBot",
    "HeadlessChrome", "PhantomJS", "Puppeteer", "Playwright",
    "python-requests", "curl/", "Wget/", "Go-http-client", "okhttp",
    # 通用尾巴 — 純小寫字根易匹配
    "bot", "crawler", "spider", "fetcher",
]


def _has_table(name: str) -> bool:
    return name in inspect(engine).get_table_names()


def run(dry_run: bool = True) -> dict:
    if not _has_table("referrals") or not _has_table("user_plans"):
        return {"status": "skipped"}

    # 1. 算多少 bot rows
    where_clauses = " OR ".join(
        ["visitor_user_agent LIKE :kw{}".format(i) for i in range(len(BOT_KEYWORDS))]
    )
    params = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(BOT_KEYWORDS)}
    # 空 UA 也算 bot
    where_full = f"({where_clauses}) OR visitor_user_agent IS NULL OR visitor_user_agent = ''"

    with engine.begin() as conn:
        bot_count = conn.execute(
            text(f"SELECT COUNT(*) FROM referrals WHERE {where_full}"),
            params,
        ).scalar() or 0
        granted_bot_count = conn.execute(
            text(f"SELECT COUNT(*) FROM referrals WHERE reward_granted=1 AND ({where_full})"),
            params,
        ).scalar() or 0

    if bot_count == 0:
        logger.info("[migration:021] no bot referrals to clean, skip")
        return {"status": "skipped"}

    if dry_run:
        print(f"DRY RUN — Migration 021: would delete {bot_count} bot rows "
              f"({granted_bot_count} of which were granted)")
        return {"status": "dry-run", "bot_rows": bot_count, "granted_bot": granted_bot_count}

    # 2. 刪 bot rows
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM referrals WHERE {where_full}"), params)
        # 3. 重算每個 referrer 的 total_share_count = 剩下 reward_granted=True 的筆數
        conn.execute(text(
            "UPDATE user_plans SET total_share_count = ("
            "  SELECT COUNT(*) FROM referrals "
            "  WHERE referrals.referrer_user_id = user_plans.user_id "
            "  AND referrals.reward_granted = 1"
            ")"
        ))

    logger.info(
        "[migration:021] cleaned %d bot referrals (%d were 'granted'); "
        "recomputed user_plans.total_share_count",
        bot_count, granted_bot_count,
    )
    return {
        "status": "ok",
        "bot_rows": bot_count,
        "granted_bot": granted_bot_count,
    }


if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    out = run(dry_run=not args.apply)
    print(f"\nResult: {out}")
