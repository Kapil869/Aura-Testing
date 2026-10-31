"""Saved request and environment ownership regression coverage."""

from uuid import uuid4

from tests.dashboard.test_multi_user import _register


def test_saved_request_and_environment_cannot_cross_accounts(live_dashboard):
    password = "workbench-password-123"
    owner = _register(live_dashboard, f"request-owner-{uuid4().hex[:8]}", password)
    other = _register(live_dashboard, f"request-other-{uuid4().hex[:8]}", password)
    env = owner.post(
        f"{live_dashboard}/api/environments",
        json={"name": "owned", "base_url": f"{live_dashboard}/"},
        timeout=10,
    ).json()
    saved = owner.post(
        f"{live_dashboard}/api/requests",
        json={"name": "owned-request", "method": "GET", "url": "/demo/health", "environment_id": env["id"]},
        timeout=10,
    ).json()
    assert other.get(f"{live_dashboard}/api/requests/{saved['id']}", timeout=10).status_code == 404
    assert other.post(f"{live_dashboard}/api/requests/{saved['id']}/execute", timeout=10).status_code == 404
    assert (
        other.post(
            f"{live_dashboard}/api/requests",
            json={"name": "stolen", "method": "GET", "url": "/demo/health", "environment_id": env["id"]},
            timeout=10,
        ).status_code
        == 404
    )
