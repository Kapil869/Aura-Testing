"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.config import Settings
from app.core.security import UrlPolicy, verify_session_token

COOKIE_NAME = "apitd_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "api-dashboard"


def get_cfg(request: Request) -> Settings:
    return request.app.state.settings


def url_policy(settings: Settings) -> UrlPolicy:
    return UrlPolicy(
        allow_local=settings.allow_local_targets,
        allow_private=settings.allow_private_targets,
        allow_external=settings.allow_external_targets,
        external_allowlist=settings.external_allowlist,
    )


def current_user(request: Request) -> str:
    """Require a valid session cookie. State-changing calls also need the CSRF header."""
    user = verify_session_token(request.cookies.get(COOKIE_NAME), request.app.state.secret_key)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status_code=403, detail="Missing CSRF header.")
    return user
