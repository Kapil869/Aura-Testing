"""Environment ownership regression coverage."""

from uuid import uuid4

from tests.dashboard.test_multi_user import _register


def test_environment_isolation(live_dashboard):
    password = "environment-password-123"
    owner = _register(live_dashboard, f"env-owner-{uuid4().hex[:8]}", password)
    other = _register(live_dashboard, f"env-other-{uuid4().hex[:8]}", password)
    created = owner.post(
        f"{live_dashboard}/api/environments",
        json={"name": "owned", "base_url": f"{live_dashboard}/"},
        timeout=10,
    )
    assert created.status_code == 201
    env_id = created.json()["id"]
    assert (
        other.put(
            f"{live_dashboard}/api/environments/{env_id}",
            json={"name": "stolen", "base_url": f"{live_dashboard}/"},
            timeout=10,
        ).status_code
        == 404
    )
    assert other.delete(f"{live_dashboard}/api/environments/{env_id}", timeout=10).status_code == 404
