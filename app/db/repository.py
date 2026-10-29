"""Data-access layer. Functions take a Session; callers own the transaction."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import decrypt_headers, encrypt_headers
from app.db import models as m

FINISHED = ("passed", "failed", "error", "timeout")


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.replace(microsecond=0).isoformat() + "Z"


# ----------------------------- users ---------------------------------------
def get_user(db: Session, username: str) -> m.User | None:
    return db.scalar(select(m.User).where(m.User.username == username))


def create_user(db: Session, username: str, password_hash: str) -> m.User:
    user = m.User(username=username, password_hash=password_hash)
    db.add(user)
    db.flush()
    return user


def count_users(db: Session) -> int:
    return db.scalar(select(func.count(m.User.id))) or 0


# ----------------------------- environments --------------------------------
def list_environments(db: Session) -> Sequence[m.Environment]:
    return db.scalars(select(m.Environment).order_by(m.Environment.name)).all()


def get_environment(db: Session, env_id: int) -> m.Environment | None:
    return db.get(m.Environment, env_id)


def get_environment_by_name(db: Session, name: str) -> m.Environment | None:
    return db.scalar(select(m.Environment).where(m.Environment.name == name))


def save_environment(db: Session, env: m.Environment | None, data: dict[str, Any], secret_key: str) -> m.Environment:
    env = env or m.Environment()
    env.name = data["name"]
    env.base_url = data["base_url"].rstrip("/")
    env.description = data.get("description", "")
    env.allow_external = bool(data.get("allow_external", False))
    incoming = data.get("headers") or {}
    existing = json.loads(env.headers_json or "{}") if env.id and env.headers_json else {}
    preserved = {key: existing[key] for key in existing if key in incoming and incoming[key] == "***REDACTED***"}
    preserved.update({key: value for key, value in incoming.items() if value != "***REDACTED***"})
    env.headers_json = json.dumps(encrypt_headers(preserved, secret_key))
    db.add(env)
    db.flush()
    return env


def delete_environment(db: Session, env: m.Environment) -> None:
    db.delete(env)
    db.flush()


def encrypt_legacy_environment_headers(db: Session, secret_key: str) -> int:
    """Upgrade plaintext sensitive environment headers in existing databases."""
    changed = 0
    for env in db.scalars(select(m.Environment)).all():
        headers = json.loads(env.headers_json or "{}")
        encrypted = encrypt_headers(decrypt_headers(headers, secret_key), secret_key)
        if encrypted != headers:
            env.headers_json = json.dumps(encrypted)
            changed += 1
    return changed


# ----------------------------- saved requests ------------------------------
def list_saved_requests(db: Session) -> Sequence[m.SavedRequest]:
    return db.scalars(select(m.SavedRequest).order_by(m.SavedRequest.name)).all()


def get_saved_request(db: Session, req_id: int) -> m.SavedRequest | None:
    return db.get(m.SavedRequest, req_id)


def save_request(db: Session, obj: m.SavedRequest | None, data: dict[str, Any]) -> m.SavedRequest:
    obj = obj or m.SavedRequest()
    obj.name = data["name"]
    obj.method = data["method"]
    obj.url = data["url"]
    obj.params_json = json.dumps(data.get("params") or {})
    obj.headers_json = json.dumps(data.get("headers") or {})
    obj.body_json = None if data.get("body") is None else json.dumps(data["body"])
    obj.timeout = data.get("timeout", 10.0)
    obj.auth_type = data.get("auth_type", "none")
    obj.auth_username = data.get("auth_username", "")
    obj.auth_header_name = data.get("auth_header_name", "X-API-Key")
    if "auth_secret_enc" in data:  # only touched when explicitly supplied
        obj.auth_secret_enc = data["auth_secret_enc"]
    obj.environment_id = data.get("environment_id")
    db.add(obj)
    db.flush()
    return obj


def delete_saved_request(db: Session, obj: m.SavedRequest) -> None:
    db.delete(obj)
    db.flush()


# ----------------------------- test runs -----------------------------------
def create_run(db: Session, **fields: Any) -> m.TestRun:
    run = m.TestRun(**fields)
    db.add(run)
    db.flush()
    return run


def get_run(db: Session, run_id: int) -> m.TestRun | None:
    return db.get(m.TestRun, run_id)


def count_active_runs(db: Session) -> int:
    return db.scalar(select(func.count(m.TestRun.id)).where(m.TestRun.status.in_(("queued", "running")))) or 0


def mark_interrupted_runs(db: Session) -> int:
    runs = db.scalars(select(m.TestRun).where(m.TestRun.status.in_(("queued", "running")))).all()
    for run in runs:
        run.status = "error"
        run.message = "Interrupted: the dashboard restarted before this run finished."
        run.finished_at = m.utcnow()
    return len(runs)


_SORTS = {
    "created_at": m.TestRun.created_at,
    "duration": m.TestRun.duration_s,
    "total": m.TestRun.total,
    "failed": m.TestRun.failed,
    "suite": m.TestRun.suite,
    "status": m.TestRun.status,
}


def list_runs(
    db: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    suite: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 25,
    offset: int = 0,
) -> tuple[Sequence[m.TestRun], int]:
    stmt = select(m.TestRun)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(m.TestRun.suite.ilike(like), m.TestRun.target_url.ilike(like), m.TestRun.message.ilike(like)))
    if status:
        stmt = stmt.where(m.TestRun.status == status)
    if suite:
        stmt = stmt.where(m.TestRun.suite == suite)
    if date_from:
        stmt = stmt.where(m.TestRun.created_at >= date_from)
    if date_to:
        stmt = stmt.where(m.TestRun.created_at < date_to + timedelta(days=1))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = _SORTS.get(sort, m.TestRun.created_at)
    stmt = stmt.order_by(column.asc() if order == "asc" else column.desc(), m.TestRun.id.desc())
    return db.scalars(stmt.limit(limit).offset(offset)).all(), total


def runs_since(db: Session, since: datetime) -> Sequence[m.TestRun]:
    return db.scalars(
        select(m.TestRun).where(m.TestRun.created_at >= since, m.TestRun.status.in_(FINISHED)).order_by(m.TestRun.created_at.asc())
    ).all()


def delete_run(db: Session, run: m.TestRun) -> None:
    db.delete(run)
    db.flush()


def add_results(db: Session, run_id: int, rows: list[dict[str, Any]]) -> None:
    db.add_all([m.TestResult(run_id=run_id, **row) for row in rows])
    db.flush()


def get_result(db: Session, result_id: int) -> m.TestResult | None:
    return db.get(m.TestResult, result_id)


def durations_for_runs(db: Session, run_ids: list[int]) -> dict[int, list[float]]:
    if not run_ids:
        return {}
    out: dict[int, list[float]] = {rid: [] for rid in run_ids}
    rows = db.execute(
        select(m.TestResult.run_id, m.TestResult.duration_s).where(m.TestResult.run_id.in_(run_ids), m.TestResult.outcome != "skipped")
    ).all()
    for rid, dur in rows:
        out[rid].append(float(dur or 0.0))
    return out


def outcome_history(db: Session, last_runs: int = 30) -> dict[str, list[str]]:
    """nodeid -> outcomes in chronological order across the most recent finished runs."""
    run_ids = db.scalars(select(m.TestRun.id).where(m.TestRun.status.in_(FINISHED)).order_by(m.TestRun.id.desc()).limit(last_runs)).all()
    if not run_ids:
        return {}
    rows = db.execute(
        select(m.TestResult.nodeid, m.TestResult.outcome)
        .where(m.TestResult.run_id.in_(run_ids), m.TestResult.outcome != "skipped")
        .order_by(m.TestResult.run_id.asc(), m.TestResult.id.asc())
    ).all()
    hist: dict[str, list[str]] = {}
    for nodeid, outcome in rows:
        hist.setdefault(nodeid, []).append(outcome)
    return hist


# ----------------------------- performance runs ----------------------------
def create_perf_run(db: Session, **fields: Any) -> m.PerfRun:
    perf = m.PerfRun(**fields)
    db.add(perf)
    db.flush()
    return perf


def list_perf_runs(db: Session, limit: int = 20) -> Sequence[m.PerfRun]:
    return db.scalars(select(m.PerfRun).order_by(m.PerfRun.id.desc()).limit(limit)).all()


def get_perf_run(db: Session, perf_id: int) -> m.PerfRun | None:
    return db.get(m.PerfRun, perf_id)


# ----------------------------- AI history ----------------------------------
def create_analysis(db: Session, **fields: Any) -> m.AIAnalysis:
    obj = m.AIAnalysis(**fields)
    db.add(obj)
    db.flush()
    return obj


def list_analyses(db: Session, kind: str | None = None, limit: int = 50) -> Sequence[m.AIAnalysis]:
    stmt = select(m.AIAnalysis).order_by(m.AIAnalysis.id.desc()).limit(limit)
    if kind:
        stmt = stmt.where(m.AIAnalysis.kind == kind)
    return db.scalars(stmt).all()
