import socket
import uuid

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def test_slow_response_triggers_client_timeout(base_url):
    with pytest.raises(requests.exceptions.Timeout):
        requests.get(f"{base_url}/demo/slow", params={"ms": 1500}, timeout=0.3)


def test_slow_response_within_timeout_succeeds(base_url):
    resp = requests.get(f"{base_url}/demo/slow", params={"ms": 100}, timeout=3)
    assert resp.status_code == 200
    assert resp.elapsed.total_seconds() >= 0.09


def test_slow_parameter_upper_bound_enforced(base_url, http):
    assert http.get(f"{base_url}/demo/slow", params={"ms": 5001}).status_code == 422


def test_connection_refused_raises_connection_error():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://127.0.0.1:{closed_port}/", timeout=2)


@pytest.mark.parametrize("code", [200, 201, 400, 401, 403, 404, 409, 500, 502, 503])
def test_status_endpoint_returns_requested_code(base_url, http, code):
    assert http.get(f"{base_url}/demo/status/{code}").status_code == code


def test_204_has_empty_body(base_url, http):
    resp = http.get(f"{base_url}/demo/status/204")
    assert resp.status_code == 204
    assert resp.content == b""


@pytest.mark.parametrize("code", [100, 199, 600])
def test_status_out_of_range_rejected(base_url, http, code):
    assert http.get(f"{base_url}/demo/status/{code}").status_code == 422


def test_protected_without_token_is_401(base_url, http):
    resp = http.get(f"{base_url}/demo/protected")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_protected_with_wrong_token_is_403(base_url, http):
    assert http.get(f"{base_url}/demo/protected", headers={"Authorization": "Bearer wrong"}).status_code == 403


def test_protected_with_valid_token_is_200(base_url, http, demo_token):
    resp = http.get(f"{base_url}/demo/protected", headers={"Authorization": f"Bearer {demo_token}"})
    assert resp.status_code == 200


def test_echo_redacts_sensitive_headers(base_url, http):
    resp = http.post(f"{base_url}/demo/echo", json={"a": 1}, headers={"Authorization": "Bearer super-secret"})
    body = resp.json()
    assert body["json"] == {"a": 1}
    assert body["headers"]["authorization"] == "***REDACTED***"


def test_client_retry_recovers_from_intermittent_503(base_url):
    """Every 3rd call to /demo/flaky fails; a retrying client should still succeed."""
    session = requests.Session()
    retry = Retry(total=2, status_forcelist=[503], backoff_factor=0.05, allowed_methods=["GET"])
    session.mount("http://", HTTPAdapter(max_retries=retry))
    key = f"retry-{uuid.uuid4().hex[:8]}"
    for _ in range(4):
        assert session.get(f"{base_url}/demo/flaky", params={"key": key}, timeout=3).status_code == 200
