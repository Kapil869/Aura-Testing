"""Deterministic local demo API used as the default test target.

It is intentionally simple, in-memory and unauthenticated (except /demo/protected)
so that the bundled test suites work offline. Disable with ENABLE_DEMO_API=false.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.core.security import redact_headers

router = APIRouter(prefix="/demo", tags=["demo-api"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_lock = threading.Lock()
_users: dict[int, dict[str, Any]] = {}
_next_id = 1
_flaky_counters: dict[str, int] = {}


def _seed() -> None:
    """Reset users only; flaky counters persist so flakiness varies between runs."""
    global _next_id
    _users.clear()
    _next_id = 1
    for name, email, age in (("Ada Lovelace", "ada@example.com", 36), ("Grace Hopper", "grace@example.com", 85)):
        _users[_next_id] = {"id": _next_id, "name": name, "email": email, "age": age}
        _next_id += 1


_seed()


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: str = Field(max_length=120)
    age: int | None = Field(default=None, ge=0, le=120)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        if not _EMAIL.match(v):
            raise ValueError("invalid email address")
        return v


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    email: str | None = Field(default=None, max_length=120)
    age: int | None = Field(default=None, ge=0, le=120)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str | None) -> str | None:
        if v is not None and not _EMAIL.match(v):
            raise ValueError("invalid email address")
        return v


@router.get("/health")
def health():
    return {"status": "ok", "service": "demo-api", "time": datetime.now(timezone.utc).isoformat()}


@router.post("/reset")
def reset():
    with _lock:
        _seed()
    return {"status": "reset", "users": len(_users)}


@router.get("/users")
def list_users(
    response: Response, limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0), q: str | None = Query(None, max_length=50)
):
    with _lock:
        items = [u for u in _users.values() if not q or q.lower() in u["name"].lower()]
    response.headers["X-Total-Count"] = str(len(items))
    return {"items": items[offset : offset + limit], "total": len(items), "limit": limit, "offset": offset}


@router.post("/users", status_code=201)
def create_user(body: UserIn, response: Response):
    global _next_id
    with _lock:
        if any(u["email"].lower() == body.email.lower() for u in _users.values()):
            raise HTTPException(409, "email already exists")
        user = {"id": _next_id, **body.model_dump()}
        _users[_next_id] = user
        _next_id += 1
    response.headers["Location"] = f"/demo/users/{user['id']}"
    return user


@router.get("/users/{user_id}")
def get_user(user_id: int):
    with _lock:
        user = _users.get(user_id)
    if not user:
        raise HTTPException(404, "user not found")
    return user


@router.put("/users/{user_id}")
def replace_user(user_id: int, body: UserIn):
    with _lock:
        if user_id not in _users:
            raise HTTPException(404, "user not found")
        _users[user_id] = {"id": user_id, **body.model_dump()}
        return _users[user_id]


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "no fields to update")
    with _lock:
        if user_id not in _users:
            raise HTTPException(404, "user not found")
        _users[user_id].update(changes)
        return _users[user_id]


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int):
    with _lock:
        if _users.pop(user_id, None) is None:
            raise HTTPException(404, "user not found")
    return Response(status_code=204)


@router.get("/status/{code}")
def status_code(code: int):
    if not 200 <= code <= 599:
        raise HTTPException(422, "code must be between 200 and 599")
    if code in (204, 304):
        return Response(status_code=code)
    return Response(content=f'{{"status": {code}}}', status_code=code, media_type="application/json")


@router.get("/slow")
def slow(ms: int = Query(0, ge=0, le=5000)):
    time.sleep(ms / 1000)
    return {"slept_ms": ms}


@router.get("/flaky")
def flaky(key: str = Query("default", max_length=40, pattern=r"^[A-Za-z0-9_-]+$")):
    """Deterministic intermittent failure: every 3rd call for a key returns 503."""
    with _lock:
        _flaky_counters[key] = _flaky_counters.get(key, 0) + 1
        count = _flaky_counters[key]
    if count % 3 == 0:
        raise HTTPException(503, f"simulated outage (call #{count})")
    return {"call": count, "ok": True}


@router.get("/protected")
def protected(authorization: str | None = Header(None)):
    expected = get_settings().demo_api_token
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    if authorization[7:].strip() != expected:
        raise HTTPException(403, "invalid token")
    return {"message": "access granted"}


@router.api_route("/echo", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def echo(request: Request):
    raw = await request.body()
    try:
        payload = await request.json() if raw else None
    except ValueError:
        payload = None
    return {
        "method": request.method,
        "query": dict(request.query_params),
        "headers": redact_headers({k: v for k, v in request.headers.items()}),
        "json": payload,
        "body_bytes": len(raw),
    }
