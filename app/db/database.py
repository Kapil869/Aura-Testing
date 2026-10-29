"""SQLAlchemy engine/session setup. The SQLite file is created on first startup."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import ROOT_DIR


class Base(DeclarativeBase):
    pass


engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def _resolve_url(url: str) -> str:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////") and ":memory:" not in url:
        raw = Path(url[len(prefix) :])
        if not raw.is_absolute():
            raw = (ROOT_DIR / raw).resolve()
        raw.parent.mkdir(parents=True, exist_ok=True)
        return f"{prefix}{raw.as_posix()}"
    return url


def init_db(url: str) -> Engine:
    """Create the engine and all tables (idempotent)."""
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    resolved = _resolve_url(url)
    kwargs = {"connect_args": {"check_same_thread": False}} if resolved.startswith("sqlite") else {}
    engine = create_engine(resolved, future=True, **kwargs)

    if resolved.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    from app.db import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    return engine


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    assert SessionLocal is not None, "Database not initialised"
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers."""
    assert SessionLocal is not None, "Database not initialised"
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
