"""Application settings loaded from environment / .env file via Pydantic v2."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Central settings object — read once at startup, inject elsewhere."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "ETF 觀察室"
    app_brand_en: str = "ETF Watch"
    app_brand_full: str = "ETF 觀察室 · ETF Watch"
    app_env: str = Field(default="dev", description="dev / prod")
    debug: bool = True

    host: str = "127.0.0.1"
    port: int = 8000

    database_url: str = Field(
        default=f"sqlite:///{(DATA_DIR / 'etf.db').as_posix()}",
        description="SQLAlchemy DB URL — SQLite single file by default.",
    )

    scheduler_timezone: str = "Asia/Taipei"
    daily_fetch_hour: int = 14
    daily_fetch_minute: int = 30

    finmind_api_token: str | None = Field(
        default=None,
        description="FinMind API token — 免費版可空,填了拿較高 rate limit",
    )

    telegram_bot_token: str | None = None
    telegram_admin_chat_id: str | None = None

    secret_key: str = "change-me-in-production"

    # 後台 /admin/login 密碼。占位值 — 部署前請改 env。
    admin_password: str = Field(default="CHANGE_ME", description="ADMIN_PASSWORD env var,後台登入用")


settings = Settings()
