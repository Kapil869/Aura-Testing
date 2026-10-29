import pytest


@pytest.fixture(scope="session", autouse=True)
def reset_demo_state(base_url, http):
    """Start every run from the same seed data (flaky counters are kept on purpose)."""
    http.post(f"{base_url}/demo/reset")


@pytest.fixture
def created_user(base_url, http):
    """Create a throw-away user and delete it afterwards."""
    from tests.helpers import unique_email

    resp = http.post(f"{base_url}/demo/users", json={"name": "Fixture User", "email": unique_email("fixture"), "age": 30})
    assert resp.status_code == 201, resp.text
    user = resp.json()
    yield user
    http.delete(f"{base_url}/demo/users/{user['id']}")
