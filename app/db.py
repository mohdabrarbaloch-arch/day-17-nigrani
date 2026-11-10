"""Database session plumbing.

One engine, one session factory, one dependency. DATABASE_URL wins; otherwise
we fall back to a local SQLite file so `uvicorn app.main:app` works with zero
setup.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DEFAULT_SQLITE = "sqlite:///./nigrani.db"


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", DEFAULT_SQLITE)
    # Heroku-style postgres:// is not understood by SQLAlchemy 2.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = _database_url()

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Create tables. Fine for this project; a real deployment would use Alembic."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
