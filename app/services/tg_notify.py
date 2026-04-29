"""Telegram 推送 — 給 daily_report_job 與未來告警用。

紀律 #18:不准把 token 漏進 log。httpx 例外 message 含 URL,
URL 含 token,raise / log 前必先 redact。
"""
from __future__ import annotations

import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


_TOKEN_RE = re.compile(r"/bot[0-9A-Za-z:_-]+/")


def _redact(s: str) -> str:
    """把 URL 裡的 /bot{TOKEN}/ 換成 /bot***/"""
    return _TOKEN_RE.sub("/bot***/", s or "")


def send_message(text: str, chat_id: str | None = None, parse_mode: str = "HTML") -> bool:
    """送 Telegram 訊息。回 True / False,失敗只 log warning,不 raise。

    text 上限 ~4096 chars(Telegram 限制),超過自動截。
    """
    token = settings.telegram_bot_token
    chat = chat_id or settings.telegram_admin_chat_id
    if not token or not chat:
        logger.warning("[tg_notify] TELEGRAM_BOT_TOKEN 或 TELEGRAM_ADMIN_CHAT_ID 沒設")
        return False

    payload = {
        "chat_id": chat,
        "text": text[:4000],
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=15.0,
        )
        if r.status_code == 200 and r.json().get("ok"):
            return True
        logger.warning("[tg_notify] HTTP %d body=%s", r.status_code, _redact(r.text)[:300])
        return False
    except Exception as e:
        logger.warning("[tg_notify] exception: %s", _redact(str(e))[:300])
        return False
