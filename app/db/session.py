"""
Database connection.

Uses SQLite by default (zero-config, works everywhere), but the only thing
that changes for Postgres is the URL string — every model and query is
standard SQLAlchemy with no SQLite-specific tricks.

    SQLite:   sqlite:///data/pulse_foundry.db
    Postgres: postgresql://user:pass@host/pulse_foundry

Set the DATABASE_URL environment variable to override.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

_DEFAULT_DB = "sqlite:///" + str(
    Path(__file__).resolve().parents[2] / "data" / "pulse_foundry.db"
)

DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_DB)

engine = create_engine(
    DATABASE_URL,
    # SQLite needs these for proper concurrency; they're harmless on Postgres.
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False,
    future=True,
)

# Turn on WAL mode for SQLite so reads don't block writes.
if "sqlite" in DATABASE_URL:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Session:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_tables() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    Base.metadata.create_all(bind=engine)


def drop_tables() -> None:
    """Drop everything. For testing only."""
    Base.metadata.drop_all(bind=engine)
