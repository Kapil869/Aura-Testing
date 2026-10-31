"""Test suites and run history."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_cfg, url_policy
from app.api.schemas import RunIn, ser_result, ser_run
from app.config import Settings
from app.core.security import UrlBlocked, evaluate_url
from app.core.test_runner import SUITES
from app.db import repository as repo
from app.db.database import get_db
from app.db.models import User

router = APIRouter(prefix="/api", tags=["runs"], dependencies=[Depends(current_user)])


@router.get("/suites")
def list_suites():
    return [{"key": k, "label": v["label"], "description": v["description"]} for k, v in SUITES.items()]


@router.get("/runs")
def list_runs(
    q: str | None = Query(None, max_length=100),
    status: Literal["queued", "running", "passed", "failed", "error", "timeout"] | None = None,
    suite: str | None = Query(None, max_length=60),
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    sort: Literal["created_at", "duration", "total", "failed", "suite", "status"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    def parse(value: str | None):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, f"Invalid date '{value}'; use YYYY-MM-DD.")

    items, total = repo.list_runs(
        db,
        user.id,
        q=q,
        status=status,
        suite=suite,
        date_from=parse(date_from),
        date_to=parse(date_to),
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return {"items": [ser_run(r) for r in items], "total": total, "limit": limit, "offset": offset}


@router.post("/runs", status_code=202)
def start_run(
    body: RunIn, request: Request, db: Session = Depends(get_db), cfg: Settings = Depends(get_cfg), user: User = Depends(current_user)
):
    key = f"{user.id}:{request.client.host if request.client else 'unknown'}"
    limiter = request.app.state.run_limiter
    if limiter.blocked(key):
        raise HTTPException(429, "Too many test executions. Try again later.")
    limiter.record_failure(key)
    if body.suite not in SUITES:
        raise HTTPException(422, f"Unknown suite '{body.suite}'. Allowed: {', '.join(SUITES)}.")
    if not cfg.enable_demo_api and not body.environment_id and not cfg.test_target_url:
        raise HTTPException(409, "The demo API is disabled; choose an environment or set TEST_TARGET_URL.")
    if repo.count_active_runs(db, user.id) >= cfg.max_active_runs or repo.count_active_runs(db) >= cfg.max_active_runs:
        raise HTTPException(429, f"Too many runs queued or running (limit {cfg.max_active_runs}). Wait for one to finish.")

    external_ok = body.authorized_external
    if body.environment_id:
        env = repo.get_environment(db, body.environment_id, user.id)
        if not env:
            raise HTTPException(404, "Environment not found.")
        target, external_ok = env.base_url, body.authorized_external and env.allow_external
    else:
        target = cfg.test_target_url or str(request.base_url).rstrip("/")
    try:
        evaluate_url(target, url_policy(cfg), authorized_external=external_ok)
    except UrlBlocked as exc:
        raise HTTPException(400, str(exc))

    run = repo.create_run(
        db, user.id, suite=body.suite, status="queued", target_url=target, environment_id=body.environment_id, triggered_by=user.username
    )
    db.commit()
    request.app.state.run_manager.submit(run.id, body.suite, target.rstrip("/"))
    return ser_run(run)


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = repo.get_run(db, run_id, user.id)
    if not run:
        raise HTTPException(404, "Run not found.")
    return ser_run(run, with_results=True)


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = repo.get_run(db, run_id, user.id)
    if not run:
        raise HTTPException(404, "Run not found.")
    if run.status in ("queued", "running"):
        raise HTTPException(409, "Cannot delete a run that is still active.")
    repo.delete_run(db, run)
    db.commit()


@router.get("/results/{result_id}")
def get_result(result_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    res = repo.get_result(db, result_id, user.id)
    if not res:
        raise HTTPException(404, "Result not found.")
    return ser_result(res)
