from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_cfg, url_policy
from app.api.schemas import PerfIn, ser_perf
from app.config import Settings
from app.core.security import UrlBlocked
from app.db import repository as repo
from app.db.database import get_db
from app.services.perf_service import PerfBusy, run_load_test

router = APIRouter(prefix="/api/perf", tags=["performance"], dependencies=[Depends(current_user)])


@router.get("/limits")
def limits(cfg: Settings = Depends(get_cfg)):
    return {
        "enabled": cfg.perf_enabled,
        "max_requests": cfg.perf_max_requests,
        "max_concurrency": cfg.perf_max_concurrency,
        "max_duration_s": cfg.perf_max_duration_seconds,
        "external_enabled": cfg.allow_external_targets,
    }


@router.post("/run", status_code=201)
def run(body: PerfIn, db: Session = Depends(get_db), cfg: Settings = Depends(get_cfg)):
    if not cfg.perf_enabled:
        raise HTTPException(403, "Performance testing is disabled (PERF_TESTING_ENABLED=false).")
    if body.total_requests > cfg.perf_max_requests:
        raise HTTPException(422, f"total_requests exceeds the configured limit of {cfg.perf_max_requests}.")
    if body.concurrency > cfg.perf_max_concurrency:
        raise HTTPException(422, f"concurrency exceeds the configured limit of {cfg.perf_max_concurrency}.")
    url, external_ok = body.url, body.authorized_external
    if body.environment_id:
        env = repo.get_environment(db, body.environment_id)
        if not env:
            raise HTTPException(404, "Environment not found.")
        if url.startswith("/"):
            url = env.base_url.rstrip("/") + url
        external_ok = external_ok and env.allow_external
    try:
        result = run_load_test(
            method=body.method,
            url=url,
            total_requests=body.total_requests,
            concurrency=min(body.concurrency, body.total_requests),
            timeout=body.timeout,
            max_duration_s=cfg.perf_max_duration_seconds,
            policy=url_policy(cfg),
            authorized_external=external_ok,
            headers=body.headers,
            body=body.body,
        )
    except UrlBlocked as exc:
        raise HTTPException(400, str(exc))
    except PerfBusy as exc:
        raise HTTPException(429, str(exc))
    perf = repo.create_perf_run(
        db,
        target_url=url,
        method=body.method,
        total_requests=body.total_requests,
        concurrency=body.concurrency,
        timeout=body.timeout,
        status=result["status"],
        duration_s=result["duration_s"],
        completed=result["completed"],
        succeeded=result["succeeded"],
        failed=result["failed"],
        throughput_rps=result["throughput_rps"],
        error_rate=result["error_rate"],
        note=result["note"],
        metrics_json=json.dumps({k: result[k] for k in ("latency_ms", "status_counts", "error_kinds")}),
    )
    db.commit()
    return ser_perf(perf)


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)):
    return [ser_perf(p) for p in repo.list_perf_runs(db, 30)]


@router.get("/runs/{perf_id}")
def get_run(perf_id: int, db: Session = Depends(get_db)):
    perf = repo.get_perf_run(db, perf_id)
    if not perf:
        raise HTTPException(404, "Performance run not found.")
    return ser_perf(perf)
