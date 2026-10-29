from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import COOKIE_NAME, CSRF_HEADER, CSRF_VALUE, current_user, get_cfg
from app.api.schemas import LoginIn
from app.config import Settings
from app.core.security import hash_password, make_session_token, verify_password
from app.db import repository as repo
from app.db.database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A valid hash used to equalise timing when the username does not exist.
_DUMMY_HASH = hash_password("not-a-real-password")


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db), cfg: Settings = Depends(get_cfg)):
    limiter = request.app.state.login_limiter
    key = request.client.host if request.client else "unknown"
    if limiter.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status_code=403, detail="Missing CSRF header.")
    user = repo.get_user(db, body.username)
    valid = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not valid:
        limiter.record_failure(key)
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    limiter.reset(key)
    ttl = cfg.session_hours * 3600
    response.set_cookie(
        COOKIE_NAME,
        make_session_token(user.username, request.app.state.secret_key, ttl),
        max_age=ttl,
        httponly=True,
        samesite="strict",
        secure=cfg.cookie_secure,
        path="/",
    )
    return {"username": user.username}


@router.post("/logout")
def logout(response: Response, user: str = Depends(current_user)):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(current_user)):
    return {"username": user}
