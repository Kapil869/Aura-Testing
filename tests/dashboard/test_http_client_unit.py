"""Outbound client + load generator tests using a throw-away stdlib HTTP server (no FastAPI needed)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from app.core.security import UrlBlocked, UrlPolicy
from app.services import perf_service
from app.services.http_client import RequestFailed, build_headers, send_request
from app.services.perf_service import run_load_test

POLICY = UrlPolicy(allow_local=True)
_server = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload, headers=None):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/slow"):
            time.sleep(1.0)
            return self._send(200, {"slow": True})
        if self.path == "/redirect-ok":
            return self._send(302, {}, {"Location": "/final"})
        if self.path == "/redirect-metadata":
            return self._send(302, {}, {"Location": "http://169.254.169.254/latest"})
        if self.path == "/fail":
            return self._send(500, {"err": 1})
        if self.path == "/big":
            raw = b"x" * (300 * 1024)
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        self._send(200, {"path": self.path, "auth": self.headers.get("Authorization"), "key": self.headers.get("X-Key")})

    do_HEAD = do_GET

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._send(201, {"echo": json.loads(body or b"null"), "ctype": self.headers.get("Content-Type")})


def base():
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{_server.server_address[1]}"


def test_get_returns_status_json_and_timing():
    r = send_request(method="GET", url=base() + "/x?a=1", policy=POLICY)
    assert r["status_code"] == 200 and r["body_json"]["path"] == "/x?a=1" and r["duration_ms"] > 0


def test_post_json_body_sets_content_type():
    r = send_request(method="POST", url=base() + "/p", body={"a": 1}, policy=POLICY)
    assert r["status_code"] == 201 and r["body_json"] == {"echo": {"a": 1}, "ctype": "application/json"}


def test_auth_helpers_apply_headers():
    r = send_request(method="GET", url=base(), auth={"type": "bearer", "secret": "tok"}, policy=POLICY)
    assert r["body_json"]["auth"] == "Bearer tok"
    r = send_request(method="GET", url=base(), auth={"type": "api_key", "secret": "k1", "header_name": "X-Key"}, policy=POLICY)
    assert r["body_json"]["key"] == "k1"
    r = send_request(method="GET", url=base(), auth={"type": "basic", "username": "u", "secret": "p"}, policy=POLICY)
    assert r["body_json"]["auth"].startswith("Basic ")


def test_safe_redirect_is_followed():
    r = send_request(method="GET", url=base() + "/redirect-ok", policy=POLICY)
    assert r["status_code"] == 200 and r["redirects"] == ["/final"]


def test_redirect_to_blocked_address_is_refused():
    with pytest.raises(UrlBlocked):
        send_request(method="GET", url=base() + "/redirect-metadata", policy=POLICY)


def test_timeout_and_connection_errors_have_clear_kinds(monkeypatch):
    with pytest.raises(RequestFailed) as exc:
        send_request(method="GET", url=base() + "/slow", timeout=0.5, policy=POLICY)
    assert exc.value.kind == "timeout"

    def raise_connection_error(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "request", raise_connection_error)
    with pytest.raises(RequestFailed) as exc:
        send_request(method="GET", url=base(), timeout=1, policy=POLICY)
    assert exc.value.kind == "connection"


def test_large_bodies_are_truncated():
    r = send_request(method="GET", url=base() + "/big", policy=POLICY)
    assert r["truncated"] and len(r["body_text"]) <= 256 * 1024


def test_disallowed_method_and_bad_headers():
    with pytest.raises(ValueError):
        send_request(method="TRACE", url=base(), policy=POLICY)
    with pytest.raises(ValueError):
        build_headers({"Bad Name": "x"}, None, False)
    with pytest.raises(ValueError):
        build_headers({"X-A": "v\r\nX-B: 1"}, None, False)
    hdrs, _ = build_headers({"Host": "evil", "Content-Length": "9", "X-Ok": "1"}, None, False)
    assert hdrs == {"X-Ok": "1"}


def test_loopback_blocked_when_policy_disallows():
    with pytest.raises(UrlBlocked):
        send_request(method="GET", url=base(), policy=UrlPolicy(allow_local=False))


def test_load_test_metrics_and_percentile_gating():
    res = run_load_test(method="GET", url=base() + "/x", total_requests=60, concurrency=6, timeout=3, max_duration_s=30, policy=POLICY)
    assert res["completed"] == 60 and res["succeeded"] == 60 and res["error_rate"] == 0.0
    lat = res["latency_ms"]
    assert lat["n"] == 60 and lat["p50"] is not None and lat["p95"] is not None and lat["p99"] is None
    assert res["throughput_rps"] > 0 and res["status_counts"] == {"200": 60}


def test_load_test_counts_http_errors_and_network_errors(monkeypatch):
    res = run_load_test(method="GET", url=base() + "/fail", total_requests=10, concurrency=2, timeout=3, max_duration_s=30, policy=POLICY)
    assert res["failed"] == 10 and res["error_rate"] == 100.0

    def raise_connection_error(*args, **kwargs):
        raise RequestFailed("connection", "Could not connect to the target.")

    monkeypatch.setattr(perf_service, "send_request", raise_connection_error)
    res = run_load_test(method="GET", url=base(), total_requests=5, concurrency=2, timeout=1, max_duration_s=30, policy=POLICY)
    assert res["failed"] == 5 and res["error_kinds"] == {"connection": 5}


def test_load_test_validates_target_and_stops_at_deadline():
    with pytest.raises(UrlBlocked):
        run_load_test(
            method="GET",
            url="http://169.254.169.254/",
            total_requests=5,
            concurrency=1,
            timeout=1,
            max_duration_s=5,
            policy=UrlPolicy(allow_private=True),
        )
    res = run_load_test(method="GET", url=base() + "/slow", total_requests=40, concurrency=1, timeout=3, max_duration_s=2, policy=POLICY)
    assert res["status"] == "stopped_early" and res["completed"] < 40 and res["note"]
