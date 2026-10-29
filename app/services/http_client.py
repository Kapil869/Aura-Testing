"""SSRF-safe outbound HTTP client used by the workbench and performance tests."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urljoin

import requests

from app.core.security import UrlBlocked, UrlPolicy, evaluate_url, redact_headers

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
MAX_BODY_BYTES = 256 * 1024
MAX_REDIRECTS = 3
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")
_FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


class RequestFailed(Exception):
    """Network-level failure with a user-friendly message."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def clamp_timeout(value: float | None, default: float = 10.0) -> float:
    return max(0.5, min(30.0, float(value if value is not None else default)))


def build_headers(
    headers: dict[str, str] | None, auth: dict[str, Any] | None, has_json_body: bool
) -> tuple[dict[str, str], tuple[str, str] | None]:
    """Validate user headers and apply authentication. Returns (headers, basic_auth)."""
    out: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if not _HEADER_NAME.match(str(name)):
            raise ValueError(f"Invalid header name: {str(name)[:40]!r}")
        if str(name).lower() in _FORBIDDEN_HEADERS:
            continue
        value = str(value)
        if "\r" in value or "\n" in value:
            raise ValueError(f"Header '{name}' contains a line break.")
        out[str(name)] = value
    basic = None
    auth = auth or {}
    kind, secret = auth.get("type", "none"), auth.get("secret") or ""
    if kind == "bearer" and secret:
        out["Authorization"] = f"Bearer {secret}"
    elif kind == "basic" and (auth.get("username") or secret):
        basic = (auth.get("username", ""), secret)
    elif kind == "api_key" and secret:
        header = auth.get("header_name") or "X-API-Key"
        if not _HEADER_NAME.match(header):
            raise ValueError("Invalid API key header name.")
        out[header] = secret
    if has_json_body and not any(k.lower() == "content-type" for k in out):
        out["Content-Type"] = "application/json"
    return out, basic


def send_request(
    *,
    method: str,
    url: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    auth: dict[str, Any] | None = None,
    body: Any = None,
    timeout: float = 10.0,
    policy: UrlPolicy,
    authorized_external: bool = False,
    session: requests.Session | None = None,
    revalidate: bool = True,
) -> dict[str, Any]:
    """Execute one request, validating every redirect hop against the URL policy."""
    method = method.upper()
    if method not in ALLOWED_METHODS:
        raise ValueError(f"Method {method} is not allowed.")
    timeout = clamp_timeout(timeout)
    hdrs, basic = build_headers(headers, auth, body is not None)
    client = session or requests
    current_url, current_method, current_body, current_params = url, method, body, params or {}
    redirects: list[str] = []
    started = time.perf_counter()

    for _hop in range(MAX_REDIRECTS + 1):
        if revalidate or _hop > 0:
            evaluate_url(current_url, policy, authorized_external)
        try:
            resp = client.request(
                current_method,
                current_url,
                params=current_params or None,
                headers=hdrs,
                auth=basic,
                json=current_body if current_body is not None else None,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout as exc:
            raise RequestFailed("timeout", f"Request timed out after {timeout:g}s.") from exc
        except requests.ConnectionError as exc:
            raise RequestFailed("connection", "Could not connect to the target (connection refused, DNS or TLS error).") from exc
        except requests.RequestException as exc:
            raise RequestFailed("request", f"Request failed: {type(exc).__name__}") from exc

        if resp.is_redirect and resp.headers.get("Location") and _hop < MAX_REDIRECTS:
            redirects.append(resp.headers["Location"])
            resp.close()
            current_url = urljoin(current_url, resp.headers["Location"])
            if resp.status_code in (301, 302, 303) and current_method != "HEAD":
                current_method, current_body = "GET", None
            current_params = {}
            continue
        break

    chunks, size, truncated = [], 0, False
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                truncated = True
                chunks.append(chunk[: max(0, MAX_BODY_BYTES - (size - len(chunk)))])
                break
            chunks.append(chunk)
    except requests.RequestException as exc:
        raise RequestFailed("read", "Connection dropped while reading the response body.") from exc
    finally:
        resp.close()
    duration_ms = (time.perf_counter() - started) * 1000
    raw = b"".join(chunks)
    text = raw.decode(resp.encoding or "utf-8", errors="replace")
    parsed = None
    if "json" in resp.headers.get("Content-Type", "").lower() and not truncated:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
    return {
        "status_code": resp.status_code,
        "reason": resp.reason,
        "headers": redact_headers(dict(resp.headers)),
        "body_text": text,
        "body_json": parsed,
        "size_bytes": size,
        "truncated": truncated,
        "duration_ms": round(duration_ms, 2),
        "final_url": current_url,
        "redirects": redirects,
    }


__all__ = ["RequestFailed", "UrlBlocked", "send_request", "clamp_timeout", "build_headers", "ALLOWED_METHODS"]
