from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_cfg
from app.api.schemas import ExplainIn, FlakyIn, GenerateIn, MissingIn, ser_analysis
from app.config import Settings
from app.core.security import redact_obj, redact_text
from app.db import repository as repo
from app.db.database import get_db
from app.db.models import User
from app.services import ai_service as ai

router = APIRouter(prefix="/api/ai", tags=["ai"], dependencies=[Depends(current_user)])


def _save(db: Session, user_id: int, kind: str, result: dict[str, Any], summary: str, run_id: int | None = None) -> dict[str, Any]:
    safe = redact_obj(result)
    row = repo.create_analysis(
        db,
        user_id,
        kind=kind,
        provider=result.get("provider", "rules"),
        input_summary=redact_text(summary, 500),
        output_json=json.dumps(safe),
        run_id=run_id,
    )
    db.commit()
    return ser_analysis(row)


@router.get("/status")
def status(cfg: Settings = Depends(get_cfg)):
    return ai.ai_status(cfg)


@router.post("/explain-failure")
def explain(body: ExplainIn, db: Session = Depends(get_db), cfg: Settings = Depends(get_cfg), user: User = Depends(current_user)):
    res = repo.get_result(db, body.result_id, user.id)
    if not res:
        raise HTTPException(404, "Result not found.")
    if res.outcome not in ("failed", "error"):
        raise HTTPException(409, "Only failed or errored tests can be analysed.")
    history = repo.outcome_history(db, 30, user.id).get(res.nodeid, [])
    evidence = {
        "test": res.nodeid,
        "outcome": res.outcome,
        "duration_s": res.duration_s,
        "message": res.message,
        "details": res.details,
        "history": history,
    }
    result = ai.explain_failure(cfg, evidence)
    result["evidence"] = {"test": res.nodeid, "message": redact_text(res.message, 500), "run_id": res.run_id}
    return _save(db, user.id, "explain_failure", result, f"{res.nodeid} (run {res.run_id})", res.run_id)


@router.post("/generate-tests")
def generate(body: GenerateIn, db: Session = Depends(get_db), cfg: Settings = Depends(get_cfg), user: User = Depends(current_user)):
    result = ai.generate_tests(cfg, body.requirement)
    return _save(db, user.id, "generate_tests", result, body.requirement[:300])


@router.post("/suggest-missing")
def suggest_missing(body: MissingIn, db: Session = Depends(get_db), cfg: Settings = Depends(get_cfg), user: User = Depends(current_user)):
    names = list(body.test_names)
    if body.run_id:
        run = repo.get_run(db, body.run_id, user.id)
        if not run:
            raise HTTPException(404, "Run not found.")
        names += [r.name for r in run.results]
    if body.saved_requests:
        names += [r.name for r in repo.list_saved_requests(db, user.id)]
    if not names:
        raise HTTPException(422, "Provide a run, saved requests or test names to analyse.")
    result = ai.suggest_missing(cfg, names)
    return _save(db, user.id, "suggest_missing", result, f"{len(names)} test names", body.run_id)


@router.post("/flaky-analysis")
def flaky(body: FlakyIn, db: Session = Depends(get_db), cfg: Settings = Depends(get_cfg), user: User = Depends(current_user)):
    history = repo.outcome_history(db, body.last_runs, user.id)
    messages: dict[str, str] = {}
    flaky_nodes = {f["nodeid"] for f in ai.detect_flaky(history)}
    if flaky_nodes:
        from sqlalchemy import select

        from app.db.models import TestResult

        rows = db.scalars(
            select(TestResult)
            .join(TestResult.run)
            .where(TestResult.nodeid.in_(flaky_nodes), TestResult.outcome.in_(("failed", "error")), TestResult.run.has(user_id=user.id))
            .order_by(TestResult.id.asc())
        ).all()
        messages = {r.nodeid: r.message for r in rows}  # latest failure wins
    result = ai.flaky_analysis(cfg, history, messages)
    return _save(db, user.id, "flaky_analysis", result, f"last {body.last_runs} runs")


@router.get("/history")
def history(kind: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [ser_analysis(a) for a in repo.list_analyses(db, user.id, kind)]
