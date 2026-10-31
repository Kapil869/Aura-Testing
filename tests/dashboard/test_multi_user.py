"""Regression coverage for account registration and owner isolation."""

from uuid import uuid4


CSRF = {"X-Requested-With": "api-dashboard"}


def _register(base, username, password):
    import requests

    client = requests.Session()
    client.headers.update(CSRF)
    response = client.post(
        f"{base}/api/auth/register",
        json={"username": username, "password": password, "confirm_password": password},
        timeout=10,
    )
    assert response.status_code == 201, response.text
    login = client.post(f"{base}/api/auth/login", json={"username": username, "password": password}, timeout=10)
    assert login.status_code == 200, login.text
    client.base = base
    return client


def test_registered_users_are_normal_and_data_isolated(live_dashboard):
    suffix = uuid4().hex[:8]
    password = "user-password-123"
    first = _register(live_dashboard, f"first-{suffix}", password)
    second = _register(live_dashboard, f"second-{suffix}", password)

    assert first.get(f"{live_dashboard}/api/auth/me", timeout=10).json() == {"username": f"first-{suffix}"}
    environment = first.post(
        f"{live_dashboard}/api/environments",
        json={"name": "private", "base_url": f"{live_dashboard}/"},
        timeout=10,
    )
    assert environment.status_code == 201, environment.text
    env_id = environment.json()["id"]

    assert second.get(f"{live_dashboard}/api/environments", timeout=10).json() == []
    assert (
        second.put(
            f"{live_dashboard}/api/environments/{env_id}",
            json={"name": "stolen", "base_url": f"{live_dashboard}/"},
            timeout=10,
        ).status_code
        == 404
    )
