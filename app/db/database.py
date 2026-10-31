"""SQLAlchemy engine/session setup. The SQLite file is created on first startup."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
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
    _migrate_legacy_schema(engine)
    return engine


def _migrate_legacy_schema(db_engine: Engine) -> None:
    """Add ownership columns and lookup indexes without dropping legacy data."""
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    with db_engine.begin() as conn:
        if "users" in tables:
            columns = {c["name"] for c in inspect(conn).get_columns("users")}
            if "role" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"))
        for table in ("environments", "saved_requests", "test_runs", "perf_runs", "ai_analyses"):
            if table not in tables:
                continue
            columns = {c["name"] for c in inspect(conn).get_columns(table)}
            if "user_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER"))
            index_name = f"ix_{table}_user_id"
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} (user_id)"))
        index_statements = (
            "CREATE INDEX IF NOT EXISTS ix_test_runs_user_created ON test_runs (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_perf_runs_user_created ON perf_runs (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_ai_analyses_user_created ON ai_analyses (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_saved_requests_user_name ON saved_requests (user_id, name)",
        )
        for statement in index_statements:
            table = statement.split(" ON ", 1)[1].split(" ", 1)[0]
            if table in tables:
                conn.execute(text(statement))


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
