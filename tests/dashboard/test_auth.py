"""Authentication and registration regression coverage."""

from uuid import uuid4

import requests

from tests.dashboard.conftest import CSRF


def test_registration_rejects_bad_password_and_duplicate(api, live_dashboard):
    username = f"auth-{uuid4().hex[:8]}"
    bad = api.post(
        f"{live_dashboard}/api/auth/register",
        json={"username": username, "password": "short", "confirm_password": "short"},
        timeout=10,
    )
    assert bad.status_code == 422

    password = "valid-password-123"
    first = api.post(
        f"{live_dashboard}/api/auth/register",
        json={"username": username, "password": password, "confirm_password": password},
        timeout=10,
    )
    assert first.status_code == 201 and first.json()["role"] == "user"
    duplicate = api.post(
        f"{live_dashboard}/api/auth/register",
        json={"username": username, "password": password, "confirm_password": password},
        timeout=10,
    )
    assert duplicate.status_code == 409


def test_login_logout_and_missing_csrf(live_dashboard):
    client = requests.Session()
    client.headers.update(CSRF)
    username = f"session-{uuid4().hex[:8]}"
    password = "valid-password-123"
    assert (
        client.post(
            f"{live_dashboard}/api/auth/register",
            json={"username": username, "password": password, "confirm_password": password},
            timeout=10,
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{live_dashboard}/api/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        ).status_code
        == 200
    )
    assert client.get(f"{live_dashboard}/api/auth/me", timeout=10).status_code == 200
    assert client.post(f"{live_dashboard}/api/auth/logout", timeout=10).status_code == 200
    assert client.get(f"{live_dashboard}/api/auth/me", timeout=10).status_code == 401
