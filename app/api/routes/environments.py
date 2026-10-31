from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.schemas import EnvironmentIn, ser_env
from app.core.security import decrypt_headers, redact_headers_for_display
from app.db import repository as repo
from app.db.database import get_db
from app.db.models import User

router = APIRouter(prefix="/api/environments", tags=["environments"], dependencies=[Depends(current_user)])


def _display(env, secret_key: str):
    headers = decrypt_headers(json.loads(env.headers_json or "{}"), secret_key)
    return ser_env(env, redact_headers_for_display(headers))


@router.get("")
def list_envs(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [_display(e, request.app.state.secret_key) for e in repo.list_environments(db, user.id)]


@router.post("", status_code=201)
def create_env(body: EnvironmentIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if repo.get_environment_by_name(db, body.name, user.id):
        raise HTTPException(409, "An environment with this name already exists.")
    try:
        env = repo.save_environment(db, None, body.model_dump(), request.app.state.secret_key, user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An environment with this name already exists.")
    return _display(env, request.app.state.secret_key)


@router.put("/{env_id}")
def update_env(env_id: int, body: EnvironmentIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    env = repo.get_environment(db, env_id, user.id)
    if not env:
        raise HTTPException(404, "Environment not found.")
    clash = repo.get_environment_by_name(db, body.name, user.id)
    if clash and clash.id != env_id:
        raise HTTPException(409, "An environment with this name already exists.")
    repo.save_environment(db, env, body.model_dump(), request.app.state.secret_key, user.id)
    db.commit()
    return _display(env, request.app.state.secret_key)


@router.delete("/{env_id}", status_code=204)
def delete_env(env_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    env = repo.get_environment(db, env_id, user.id)
    if not env:
        raise HTTPException(404, "Environment not found.")
    repo.delete_environment(db, env)
    db.commit()
