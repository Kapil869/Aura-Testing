import pytest

from tests.helpers import assert_matches_schema, unique_email


def test_create_user_returns_201_location_and_schema(base_url, http):
    resp = http.post(f"{base_url}/demo/users", json={"name": "Alan Turing", "email": unique_email(), "age": 41})
    assert resp.status_code == 201
    user = resp.json()
    assert_matches_schema(user, "user")
    assert resp.headers["Location"] == f"/demo/users/{user['id']}"
    http.delete(f"{base_url}/demo/users/{user['id']}")


def test_get_user_by_id(base_url, http, created_user):
    resp = http.get(f"{base_url}/demo/users/{created_user['id']}")
    assert resp.status_code == 200
    assert resp.json() == created_user


def test_put_replaces_user(base_url, http, created_user):
    new = {"name": "Replaced", "email": unique_email("put"), "age": 50}
    resp = http.put(f"{base_url}/demo/users/{created_user['id']}", json=new)
    assert resp.status_code == 200
    assert resp.json() == {"id": created_user["id"], **new}


def test_patch_changes_only_given_fields(base_url, http, created_user):
    resp = http.patch(f"{base_url}/demo/users/{created_user['id']}", json={"age": 31})
    assert resp.status_code == 200
    body = resp.json()
    assert body["age"] == 31
    assert body["name"] == created_user["name"]


def test_delete_then_get_returns_404(base_url, http):
    created = http.post(f"{base_url}/demo/users", json={"name": "Temp", "email": unique_email("del")}).json()
    assert http.delete(f"{base_url}/demo/users/{created['id']}").status_code == 204
    assert http.get(f"{base_url}/demo/users/{created['id']}").status_code == 404


def test_delete_twice_returns_404(base_url, http):
    created = http.post(f"{base_url}/demo/users", json={"name": "Temp2", "email": unique_email("del2")}).json()
    http.delete(f"{base_url}/demo/users/{created['id']}")
    assert http.delete(f"{base_url}/demo/users/{created['id']}").status_code == 404


@pytest.mark.parametrize("limit", [1, 2, 100])
def test_list_respects_limit(base_url, http, limit):
    body = http.get(f"{base_url}/demo/users", params={"limit": limit}).json()
    assert len(body["items"]) <= limit
    assert body["limit"] == limit


def test_list_search_filters_by_name(base_url, http):
    body = http.get(f"{base_url}/demo/users", params={"q": "lovelace"}).json()
    assert body["total"] >= 1
    assert all("lovelace" in u["name"].lower() for u in body["items"])


def test_total_count_header_matches_body(base_url, http):
    resp = http.get(f"{base_url}/demo/users")
    assert int(resp.headers["X-Total-Count"]) == resp.json()["total"]
