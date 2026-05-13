"""教學專區 — Category + Article ORM。

設計:
- categories:DB 預留 4 類,前台 tab bar 只顯示 count > 0 的分類
- articles:status / access_level / soft delete / 公開 slug 唯一
- is_new property:14 天內 publish 顯示粉紅 NEW 標籤
- reading_minutes property:約 400 中字/分鐘
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LearnCategory(Base):
    """教學專區分類(DB 預留多筆,前台動態顯示有文章的)。"""
    __tablename__ = "learn_categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="amber")  # green/blue/amber/purple
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    articles: Mapped[list["LearnArticle"]] = relationship(
        "LearnArticle", back_populates="category"
    )


class LearnArticle(Base):
    """教學文章 — Markdown 內容 + 三層存取控制 + 軟刪除。"""
    __tablename__ = "learn_articles"
    __table_args__ = (
        Index("ix_learn_articles_status_cat", "status", "category_id", "published_at"),
        Index("ix_learn_articles_status_access", "status", "access_level", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_html: Mapped[str] = mapped_column(Text, nullable=False, default="")  # cache
    category_id: Mapped[int] = mapped_column(
        ForeignKey("learn_categories.id"), nullable=False, index=True
    )
    access_level: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    # 'public' | 'login' | 'sponsor'
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    # 'draft' | 'published'
    author: Mapped[str] = mapped_column(String(40), nullable=False, default="ETF 觀察室編輯部")
    view_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # 軟刪除

    category: Mapped["LearnCategory"] = relationship(
        "LearnCategory", back_populates="articles"
    )

    @property
    def is_new(self) -> bool:
        """14 天內 publish → 顯示 NEW 標籤。"""
        if not self.published_at:
            return False
        return (datetime.now() - self.published_at).days <= 14

    @property
    def reading_minutes(self) -> int:
        """中文 400 字/分鐘粗估。"""
        if not self.content_md:
            return 1
        return max(1, len(self.content_md) // 400)

    @property
    def access_label(self) -> str:
        return {"public": "公開", "login": "登入", "sponsor": "贊助"}.get(
            self.access_level, "公開"
        )

    @property
    def access_color_class(self) -> str:
        return {
            "public": "text-gray-400",
            "login": "text-yellow-400",
            "sponsor": "text-pink-400",
        }.get(self.access_level, "text-gray-400")
