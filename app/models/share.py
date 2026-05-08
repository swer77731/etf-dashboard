"""分享 / 推薦相關 ORM models(migration 009)。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ShareClick(Base):
    """訪客從 ?ref=XXX 進來的記錄。

    流程:
    - 訪客 GET /?ref=XXX → 找出 user.referral_code=XXX → 寫一筆 row(is_valid=0)+ 設 cookie
    - 同 IP 24h 內重複 → 不重複寫
    - 訪客停留 > 30s → JS POST /api/share/visit-valid → 該 row.is_valid=1
    """
    __tablename__ = "share_clicks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    referrer_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    visitor_ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_valid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ShareClick id={self.id} ref_user_id={self.referrer_user_id} valid={self.is_valid}>"


class ShareButtonClick(Base):
    """用戶按分享按鈕的點擊記錄(FB / LINE / Threads / 複製)。

    user_id 可為 NULL — 未登入訪客也能按分享(規格:不強制登入)。
    """
    __tablename__ = "share_button_clicks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    page_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ShareButtonClick id={self.id} user={self.user_id} {self.platform}>"
