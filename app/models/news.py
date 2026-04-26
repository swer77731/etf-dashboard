"""News headline model — URL is unique to dedupe."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(512), unique=True, index=True, nullable=False)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    etf_tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<News {self.id} {self.title[:30]}>"
