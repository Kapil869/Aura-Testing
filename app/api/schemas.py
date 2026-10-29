"""Pydantic request models and serializers for responses."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.db import models as m
from app.db.repository import iso

Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class AuthIn(BaseModel):
    type: Literal["none", "bearer", "basic", "api_key"] = "none"
    username: str = Field(default="", max_length=200)
    secret: str | None = Field(default=None, max_length=4000)  # token or password; never echoed back
    header_name: str = Field(default="X-API-Key", max_length=100)


class _KV(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _stringify_maps(cls, v: Any, info):
        if info.field_name in ("params", "headers") and isinstance(v, dict):
            return {str(k)[:200]: str(val)[:4000] for k, val in list(v.items())[:50]}
        return v


class RequestIn(_KV):
    method: Method = "GET"
    url: str = Field(min_length=1, max_length=2048)
    params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    auth: AuthIn = Field(default_factory=AuthIn)
    body: Any | None = None
    timeout: float = Field(default=10.0, ge=0.5, le=30)
    environment_id: int | None = None
    authorized_external: bool = False


class SavedRequestIn(_KV):
    name: str = Field(min_length=1, max_length=120)
    method: Method = "GET"
    url: str = Field(min_length=1, max_length=2048)
    params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    auth: AuthIn = Field(default_factory=AuthIn)
    body: Any | None = None
    timeout: float = Field(default=10.0, ge=0.5, le=30)
    environment_id: int | None = None


class EnvironmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    base_url: str = Field(min_length=1, max_length=2048)
    description: str = Field(default="", max_length=500)
    allow_external: bool = False
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v.strip()

    @field_validator("headers")
    @classmethod
    def _bounded_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 50:
            raise ValueError("headers may contain at most 50 entries")
        for name, header_value in value.items():
            if len(name) > 100 or len(header_value) > 4000:
                raise ValueError("header names are limited to 100 and values to 4000 characters")
        return value


class RunIn(BaseModel):
    suite: str = Field(min_length=1, max_length=60)
    environment_id: int | None = None
    authorized_external: bool = False


class PerfIn(BaseModel):
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"] = "GET"
    url: str = Field(min_length=1, max_length=2048)
    total_requests: int = Field(default=100, ge=1, le=5000)
    concurrency: int = Field(default=5, ge=1, le=50)
    timeout: float = Field(default=5.0, ge=0.5, le=30)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None
    environment_id: int | None = None
    authorized_external: bool = False


class ExplainIn(BaseModel):
    result_id: int


class GenerateIn(BaseModel):
    requirement: str = Field(min_length=10, max_length=4000)


class MissingIn(BaseModel):
    run_id: int | None = None
    saved_requests: bool = False
    test_names: list[str] = Field(default_factory=list, max_length=500)


class FlakyIn(BaseModel):
    last_runs: int = Field(default=30, ge=4, le=200)


# ------------------------------------------------------------------ serializers
def _loads(raw: str | None, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


def ser_run(r: m.TestRun, with_results: bool = False) -> dict[str, Any]:
    data = {
        "id": r.id,
        "suite": r.suite,
        "status": r.status,
        "target_url": r.target_url,
        "environment_id": r.environment_id,
        "triggered_by": r.triggered_by,
        "created_at": iso(r.created_at),
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
        "duration_s": r.duration_s,
        "total": r.total,
        "passed": r.passed,
        "failed": r.failed,
        "errors": r.errors,
        "skipped": r.skipped,
        "retries_used": r.retries_used,
        "message": r.message,
    }
    if with_results:
        data["output_tail"] = r.output_tail
        data["results"] = [ser_result(x) for x in r.results]
    return data


def ser_result(x: m.TestResult) -> dict[str, Any]:
    return {
        "id": x.id,
        "run_id": x.run_id,
        "nodeid": x.nodeid,
        "name": x.name,
        "classname": x.classname,
        "outcome": x.outcome,
        "duration_s": x.duration_s,
        "message": x.message,
        "details": x.details,
    }


def ser_env(e: m.Environment, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": e.id,
        "name": e.name,
        "base_url": e.base_url,
        "description": e.description,
        "allow_external": e.allow_external,
        "headers": headers if headers is not None else _loads(e.headers_json, {}),
        "updated_at": iso(e.updated_at),
    }


def ser_saved(r: m.SavedRequest) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "method": r.method,
        "url": r.url,
        "params": _loads(r.params_json, {}),
        "headers": _loads(r.headers_json, {}),
        "body": _loads(r.body_json, None),
        "timeout": r.timeout,
        "auth": {
            "type": r.auth_type,
            "username": r.auth_username,
            "header_name": r.auth_header_name,
            "has_secret": bool(r.auth_secret_enc),
        },
        "environment_id": r.environment_id,
        "updated_at": iso(r.updated_at),
    }


def ser_perf(p: m.PerfRun) -> dict[str, Any]:
    return {
        "id": p.id,
        "target_url": p.target_url,
        "method": p.method,
        "total_requests": p.total_requests,
        "concurrency": p.concurrency,
        "timeout": p.timeout,
        "status": p.status,
        "duration_s": p.duration_s,
        "completed": p.completed,
        "succeeded": p.succeeded,
        "failed": p.failed,
        "throughput_rps": p.throughput_rps,
        "error_rate": p.error_rate,
        "metrics": _loads(p.metrics_json, {}),
        "note": p.note,
        "created_at": iso(p.created_at),
    }


def ser_analysis(a: m.AIAnalysis) -> dict[str, Any]:
    return {
        "id": a.id,
        "kind": a.kind,
        "provider": a.provider,
        "input_summary": a.input_summary,
        "output": _loads(a.output_json, {}),
        "run_id": a.run_id,
        "created_at": iso(a.created_at),
    }
