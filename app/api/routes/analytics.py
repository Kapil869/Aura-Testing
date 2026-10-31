from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.schemas import ser_result, ser_run
from app.db import repository as repo
from app.db.database import get_db
from app.db.models import utcnow
from app.db.models import User
from app.services import analytics_service as an

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(current_user)])


@router.get("/summary")
def summary(days: int = Query(14, ge=1, le=365), db: Session = Depends(get_db), user: User = Depends(current_user)):
    runs = repo.runs_since(db, utcnow() - timedelta(days=days), user.id)
    rows = [ser_run(r) for r in runs]
    durations = repo.durations_for_runs(db, [r["id"] for r in rows], user.id)
    per_run = []
    for r in rows:
        stats = an.latency_stats(durations.get(r["id"], []))
        per_run.append(
            {
                "run_id": r["id"],
                "created_at": r["created_at"],
                "suite": r["suite"],
                "status": r["status"],
                "n": stats["n"],
                "mean": stats["mean"],
                "p50": stats["p50"],
                "p95": stats["p95"],
                "p99": stats["p99"],
            }
        )
    all_samples = [d for ds in durations.values() for d in ds]
    perf = []
    for p in reversed(repo.list_perf_runs(db, user.id, 20)):
        lat = json.loads(p.metrics_json or "{}").get("latency_ms") or {}
        perf.append(
            {
                "id": p.id,
                "created_at": repo.iso(p.created_at),
                "p50": lat.get("p50"),
                "p95": lat.get("p95"),
                "throughput_rps": p.throughput_rps,
                "error_rate": p.error_rate,
            }
        )
    return {
        "days": days,
        "totals": an.summarize_runs(rows),
        "daily": an.daily_series(rows, min(days, 90), today=utcnow().date()),
        "latency_all_tests": an.latency_stats(all_samples),
        "per_run_latency": per_run[-40:],
        "perf_trend": perf,
        "has_data": bool(rows),
    }


@router.get("/compare")
def compare(a: int, b: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run_a, run_b = repo.get_run(db, a, user.id), repo.get_run(db, b, user.id)
    if not run_a or not run_b:
        raise HTTPException(404, "One or both runs were not found.")
    diff = an.compare_runs([ser_result(r) for r in run_a.results], [ser_result(r) for r in run_b.results])
    return {
        "a": ser_run(run_a),
        "b": ser_run(run_b),
        "diff": diff,
        "delta": {
            "passed": run_b.passed - run_a.passed,
            "failed": (run_b.failed + run_b.errors) - (run_a.failed + run_a.errors),
            "duration_s": None if run_a.duration_s is None or run_b.duration_s is None else round(run_b.duration_s - run_a.duration_s, 2),
        },
    }


@router.get("/flaky")
def flaky(last_runs: int = Query(30, ge=4, le=200), db: Session = Depends(get_db), user: User = Depends(current_user)):
    history = repo.outcome_history(db, last_runs, user.id)
    return {"runs_considered": last_runs, "flaky_tests": an.detect_flaky(history), "tests_tracked": len(history)}
