"""SQLAlchemy 2.0 engine, session factory, declarative base.

Sync engine + sync sessions — keeping it simple for SQLite.
FastAPI routes use a generator dependency to manage the session lifecycle.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


class Base(DeclarativeBase):
    """Declarative base — all ORM models inherit from this."""


def init_db() -> None:
    """Create all tables registered on Base.metadata."""
    # Importing the models package registers them on Base.metadata.
    from app import models  # noqa: F401

    logger.info("Creating tables on %s", settings.database_url)
    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency — yields a session, closes it on exit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone context manager for jobs / scripts outside the FastAPI request cycle."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
