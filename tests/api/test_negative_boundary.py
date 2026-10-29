import pytest

from tests.helpers import assert_matches_schema, unique_email


def _create(base_url, http, **overrides):
    payload = {"name": "Boundary", "email": unique_email("b"), "age": 30, **overrides}
    payload = {k: v for k, v in payload.items() if v is not ...}
    return http.post(f"{base_url}/demo/users", json=payload)


def _cleanup(base_url, http, resp):
    if resp.status_code == 201:
        http.delete(f"{base_url}/demo/users/{resp.json()['id']}")


@pytest.mark.parametrize(
    "overrides",
    [{"name": ...}, {"email": ...}, {"name": ""}, {"name": "x" * 51}, {"name": None}, {"name": 123}],
    ids=["missing-name", "missing-email", "empty-name", "name-51-chars", "null-name", "numeric-name"],
)
def test_invalid_names_rejected(base_url, http, overrides):
    assert _create(base_url, http, **overrides).status_code == 422


@pytest.mark.parametrize("name", ["x", "x" * 50], ids=["name-1-char", "name-50-chars"])
def test_name_length_boundaries_accepted(base_url, http, name):
    resp = _create(base_url, http, name=name)
    assert resp.status_code == 201
    _cleanup(base_url, http, resp)


@pytest.mark.parametrize("age", [-1, 121, 1000, "abc", 3.5], ids=["neg-1", "121", "1000", "string", "float"])
def test_invalid_ages_rejected(base_url, http, age):
    assert _create(base_url, http, age=age).status_code == 422


@pytest.mark.parametrize("age", [0, 1, 119, 120])
def test_age_boundaries_accepted(base_url, http, age):
    resp = _create(base_url, http, age=age)
    assert resp.status_code == 201
    _cleanup(base_url, http, resp)


@pytest.mark.parametrize("email", ["plain", "no-at.example.com", "a@b", "@example.com", "a b@example.com", ""])
def test_malformed_emails_rejected(base_url, http, email):
    assert _create(base_url, http, email=email).status_code == 422


def test_duplicate_email_conflicts(base_url, http):
    email = unique_email("dup")
    first = _create(base_url, http, email=email)
    assert first.status_code == 201
    second = _create(base_url, http, email=email.upper())
    assert second.status_code == 409
    assert_matches_schema(second.json(), "error")
    _cleanup(base_url, http, first)


def test_malformed_json_rejected(base_url, http):
    resp = http.post(f"{base_url}/demo/users", data="{not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 422


def test_patch_with_empty_body_rejected(base_url, http, created_user):
    assert http.patch(f"{base_url}/demo/users/{created_user['id']}", json={}).status_code == 422


def test_unknown_user_returns_404(base_url, http):
    assert http.get(f"{base_url}/demo/users/999999").status_code == 404


def test_non_numeric_id_rejected(base_url, http):
    assert http.get(f"{base_url}/demo/users/abc").status_code == 422


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"limit": 0}, 422),
        ({"limit": 101}, 422),
        ({"limit": 100}, 200),
        ({"limit": 1}, 200),
        ({"offset": -1}, 422),
        ({"offset": 0}, 200),
        ({"limit": "abc"}, 422),
    ],
)
def test_pagination_boundaries(base_url, http, params, expected):
    assert http.get(f"{base_url}/demo/users", params=params).status_code == expected


@pytest.mark.parametrize(
    "method,path", [("PATCH", "/demo/users"), ("DELETE", "/demo/users"), ("POST", "/demo/health"), ("DELETE", "/demo/health")]
)
def test_wrong_method_returns_405(base_url, http, method, path):
    assert http.request(method, f"{base_url}{path}").status_code == 405
