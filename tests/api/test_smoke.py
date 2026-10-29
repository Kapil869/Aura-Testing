import pytest

from tests.helpers import assert_matches_schema


def test_health_returns_ok(base_url, http):
    resp = http.get(f"{base_url}/demo/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_matches_schema(base_url, http):
    assert_matches_schema(http.get(f"{base_url}/demo/health").json(), "health")


def test_health_content_type_is_json(base_url, http):
    assert "application/json" in http.get(f"{base_url}/demo/health").headers["Content-Type"]


def test_health_responds_under_budget(base_url, http):
    resp = http.get(f"{base_url}/demo/health")
    assert resp.elapsed.total_seconds() < 1.0, f"took {resp.elapsed.total_seconds():.3f}s"


def test_unknown_path_returns_404(base_url, http):
    assert http.get(f"{base_url}/demo/does-not-exist").status_code == 404


def test_seeded_users_listed_with_schema(base_url, http):
    resp = http.get(f"{base_url}/demo/users")
    assert resp.status_code == 200
    body = resp.json()
    assert_matches_schema(body, "user_list")
    assert body["total"] >= 2


@pytest.mark.parametrize("path", ["/demo/health", "/demo/users", "/demo/users/1"])
def test_read_endpoints_are_fast(base_url, http, path):
    assert http.get(f"{base_url}{path}").elapsed.total_seconds() < 1.0
