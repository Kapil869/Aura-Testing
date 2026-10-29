from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.schemas import ser_run
from app.db import repository as repo
from app.db.database import get_db
from app.services import report_service as rs

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(current_user)])

_TYPES = {"html": "text/html; charset=utf-8", "json": "application/json", "csv": "text/csv; charset=utf-8"}


def _download(content: str, fmt: str, filename: str) -> Response:
    return Response(
        content,
        media_type=_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.{fmt}"',
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/runs/{run_id}")
def run_report(run_id: int, format: Literal["html", "json", "csv"] = "html", db: Session = Depends(get_db)):
    run = repo.get_run(db, run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    run_dict = ser_run(run, with_results=True)
    results = run_dict.pop("results")
    data = rs.run_report_data(run_dict, results)
    body = {"html": rs.to_html, "json": rs.to_json}.get(format, lambda d: rs.results_to_csv(d["results"]))(data)
    return _download(body, format, f"run-{run_id}-report")


@router.get("/history")
def history_report(format: Literal["json", "csv"] = "csv", limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    items, _ = repo.list_runs(db, limit=limit)
    runs = [ser_run(r) for r in items]
    body = rs.history_to_csv(runs) if format == "csv" else rs.to_json({"runs": runs})
    return _download(body, format, "run-history")
