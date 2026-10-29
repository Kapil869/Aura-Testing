from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import current_user, get_cfg
from app.config import Settings
from app.core.test_runner import SUITES
from app.services.ai_service import ai_status

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/meta", dependencies=[Depends(current_user)])
def meta(cfg: Settings = Depends(get_cfg)):
    return {
        "suites": [{"key": k, "label": v["label"], "description": v["description"]} for k, v in SUITES.items()],
        "ai": ai_status(cfg),
        "demo_api_enabled": cfg.enable_demo_api,
        "policy": {
            "allow_local": cfg.allow_local_targets,
            "allow_private": cfg.allow_private_targets,
            "allow_external": cfg.allow_external_targets,
            "allowlist": list(cfg.external_allowlist),
        },
        "limits": {
            "max_active_runs": cfg.max_active_runs,
            "test_timeout_s": cfg.test_timeout_seconds,
            "perf_max_requests": cfg.perf_max_requests,
            "perf_max_concurrency": cfg.perf_max_concurrency,
        },
    }
