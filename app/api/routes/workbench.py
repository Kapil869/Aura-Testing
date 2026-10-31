"""Request workbench: ad-hoc execution and saved request configurations."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_cfg, url_policy
from app.api.schemas import RequestIn, SavedRequestIn, ser_saved
from app.config import Settings
from app.core.security import UrlBlocked, decrypt_headers, decrypt_secret, encrypt_secret
from app.db import repository as repo
from app.db.database import get_db
from app.db.models import User
from app.services.http_client import RequestFailed, send_request

router = APIRouter(prefix="/api", tags=["workbench"], dependencies=[Depends(current_user)])


def _resolve_url(db: Session, url: str, environment_id: int | None, secret_key: str, user_id: int) -> tuple[str, dict[str, str], bool]:
    """Join relative URLs with the environment base URL. Returns (url, env_headers, env_allows_external)."""
    if environment_id is None:
        return url, {}, False
    env = repo.get_environment(db, environment_id, user_id)
    if not env:
        raise HTTPException(404, "Environment not found.")
    if url.startswith("/"):
        url = env.base_url.rstrip("/") + url
    try:
        headers = decrypt_headers(json.loads(env.headers_json or "{}"), secret_key)
    except ValueError:
        headers = {}
    return url, headers, env.allow_external


def _execute(
    db: Session,
    cfg: Settings,
    *,
    method: str,
    url: str,
    params,
    headers,
    auth: dict[str, Any],
    body,
    timeout: float,
    environment_id: int | None,
    authorized_external: bool,
    secret_key: str,
    user_id: int,
) -> dict[str, Any]:
    url, env_headers, env_external = _resolve_url(db, url, environment_id, secret_key, user_id)
    merged = {**env_headers, **(headers or {})}
    try:
        return {
            "ok": True,
            **send_request(
                method=method,
                url=url,
                params=params,
                headers=merged,
                auth=auth,
                body=body,
                timeout=timeout,
                policy=url_policy(cfg),
                authorized_external=authorized_external and (environment_id is None or env_external),
            ),
        }
    except UrlBlocked as exc:
        raise HTTPException(400, {"kind": "blocked", "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(400, {"kind": "invalid", "message": str(exc)})
    except RequestFailed as exc:
        raise HTTPException(502 if exc.kind != "timeout" else 504, {"kind": exc.kind, "message": exc.message})


@router.post("/workbench/execute")
def execute(
    body: RequestIn, request: Request, db: Session = Depends(get_db), cfg: Settings = Depends(get_cfg), user: User = Depends(current_user)
):
    return _execute(
        db,
        cfg,
        method=body.method,
        url=body.url,
        params=body.params,
        headers=body.headers,
        auth=body.auth.model_dump(),
        body=body.body,
        timeout=body.timeout,
        environment_id=body.environment_id,
        authorized_external=body.authorized_external,
        secret_key=request.app.state.secret_key,
        user_id=user.id,
    )


@router.get("/requests")
def list_requests(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [ser_saved(r) for r in repo.list_saved_requests(db, user.id)]


def _payload(body: SavedRequestIn, request: Request, existing=None) -> dict[str, Any]:
    data = {
        "name": body.name,
        "method": body.method,
        "url": body.url,
        "params": body.params,
        "headers": body.headers,
        "body": body.body,
        "timeout": body.timeout,
        "auth_type": body.auth.type,
        "auth_username": body.auth.username,
        "auth_header_name": body.auth.header_name,
        "environment_id": body.environment_id,
    }
    secret_key = request.app.state.secret_key
    if body.auth.type == "none":
        data["auth_secret_enc"] = None
    elif body.auth.secret:  # blank secret on update = keep what is stored
        data["auth_secret_enc"] = encrypt_secret(body.auth.secret, secret_key)
    elif existing is None:
        data["auth_secret_enc"] = None
    return data


@router.post("/requests", status_code=201)
def create_request(body: SavedRequestIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.environment_id and not repo.get_environment(db, body.environment_id, user.id):
        raise HTTPException(404, "Environment not found.")
    obj = repo.save_request(db, None, _payload(body, request), user.id)
    db.commit()
    return ser_saved(obj)


@router.get("/requests/{req_id}")
def get_request(req_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    obj = repo.get_saved_request(db, req_id, user.id)
    if not obj:
        raise HTTPException(404, "Saved request not found.")
    return ser_saved(obj)


@router.put("/requests/{req_id}")
def update_request(req_id: int, body: SavedRequestIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    obj = repo.get_saved_request(db, req_id, user.id)
    if not obj:
        raise HTTPException(404, "Saved request not found.")
    if body.environment_id and not repo.get_environment(db, body.environment_id, user.id):
        raise HTTPException(404, "Environment not found.")
    repo.save_request(db, obj, _payload(body, request, obj), user.id)
    db.commit()
    return ser_saved(obj)


@router.delete("/requests/{req_id}", status_code=204)
def delete_request(req_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    obj = repo.get_saved_request(db, req_id, user.id)
    if not obj:
        raise HTTPException(404, "Saved request not found.")
    repo.delete_saved_request(db, obj)
    db.commit()


@router.post("/requests/{req_id}/execute")
def execute_saved(
    req_id: int,
    request: Request,
    authorized_external: bool = False,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_cfg),
    user: User = Depends(current_user),
):
    obj = repo.get_saved_request(db, req_id, user.id)
    if not obj:
        raise HTTPException(404, "Saved request not found.")
    secret = decrypt_secret(obj.auth_secret_enc, request.app.state.secret_key) if obj.auth_secret_enc else None
    saved = ser_saved(obj)
    auth = {"type": obj.auth_type, "username": obj.auth_username, "header_name": obj.auth_header_name, "secret": secret}
    return _execute(
        db,
        cfg,
        method=obj.method,
        url=obj.url,
        params=saved["params"],
        headers=saved["headers"],
        auth=auth,
        body=saved["body"],
        timeout=obj.timeout,
        environment_id=obj.environment_id,
        authorized_external=authorized_external,
        secret_key=request.app.state.secret_key,
        user_id=user.id,
    )
