"""End-to-end tests against a real dashboard instance (see conftest.live_dashboard)."""

import json
import time

import pytest
import requests

from tests.dashboard.conftest import ADMIN_USER


def wait_for_run(api, run_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = api.get(f"{api.base}/api/runs/{run_id}", timeout=15).json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


# --------------------------------------------------------------- basics/auth
def test_index_and_health_are_public(anon):
    assert "text/html" in anon.get(f"{anon.base}/", timeout=10).headers["Content-Type"]
    assert anon.get(f"{anon.base}/api/health", timeout=10).json() == {"status": "ok"}


def test_security_headers_present(anon):
    h = anon.get(f"{anon.base}/api/health", timeout=10).headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize(
    "path", ["/api/runs", "/api/requests", "/api/environments", "/api/analytics/summary", "/api/meta", "/api/ai/history"]
)
def test_api_requires_authentication(anon, path):
    assert anon.get(f"{anon.base}{path}", timeout=10).status_code == 401


def test_login_rejects_bad_credentials_and_missing_csrf(anon):
    bad = anon.post(f"{anon.base}/api/auth/login", json={"username": ADMIN_USER, "password": "nope"}, timeout=10)
    assert bad.status_code == 401
    no_csrf = requests.post(f"{anon.base}/api/auth/login", json={"username": ADMIN_USER, "password": "nope"}, timeout=10)
    assert no_csrf.status_code == 403


def test_session_cookie_flags_and_me(api):
    assert api.get(f"{api.base}/api/auth/me", timeout=10).json() == {"username": ADMIN_USER}
    cookie = next(c for c in api.cookies if c.name == "apitd_session")
    assert cookie.has_nonstandard_attr("HttpOnly")


def test_state_changing_calls_need_csrf_header(api):
    r = requests.post(f"{api.base}/api/environments", json={"name": "x", "base_url": "http://127.0.0.1:1"}, cookies=api.cookies, timeout=10)
    assert r.status_code == 403


def test_meta_reports_policy_and_ai_mode(api):
    meta = api.get(f"{api.base}/api/meta", timeout=10).json()
    assert meta["ai"]["mode"] == "rules" and meta["policy"]["allow_external"] is False
    assert {s["key"] for s in meta["suites"]} >= {"smoke", "full_api"}


# ------------------------------------------------------------- environments
def test_environment_crud_and_validation(api):
    created = api.post(
        f"{api.base}/api/environments", json={"name": "local-demo", "base_url": f"{api.base}/", "description": "d"}, timeout=10
    )
    assert created.status_code == 201 and created.json()["base_url"] == api.base
    env_id = created.json()["id"]
    assert api.post(f"{api.base}/api/environments", json={"name": "local-demo", "base_url": api.base}, timeout=10).status_code == 409
    assert api.post(f"{api.base}/api/environments", json={"name": "bad", "base_url": "ftp://x"}, timeout=10).status_code == 422
    upd = api.put(
        f"{api.base}/api/environments/{env_id}", json={"name": "local-demo", "base_url": api.base, "description": "changed"}, timeout=10
    )
    assert upd.json()["description"] == "changed"
    assert api.delete(f"{api.base}/api/environments/{env_id}", timeout=10).status_code == 204
    assert api.delete(f"{api.base}/api/environments/{env_id}", timeout=10).status_code == 404


# ----------------------------------------------------------------- workbench
def test_workbench_executes_request_against_local_demo(api):
    r = api.post(f"{api.base}/api/workbench/execute", json={"method": "GET", "url": f"{api.base}/demo/health"}, timeout=15)
    body = r.json()
    assert r.status_code == 200 and body["status_code"] == 200 and body["body_json"]["status"] == "ok"
    assert body["duration_ms"] >= 0 and "content-type" in {k.lower() for k in body["headers"]}


def test_workbench_post_with_json_and_query(api):
    r = api.post(
        f"{api.base}/api/workbench/execute",
        json={
            "method": "POST",
            "url": f"{api.base}/demo/echo",
            "params": {"a": "1"},
            "body": {"hello": "world"},
            "auth": {"type": "bearer", "secret": "workbench-secret"},
        },
        timeout=15,
    ).json()
    echoed = r["body_json"]
    assert echoed["json"] == {"hello": "world"} and echoed["query"] == {"a": "1"}
    assert "workbench-secret" not in r["body_text"]  # echo redacts Authorization


@pytest.mark.parametrize(
    "url", ["http://169.254.169.254/latest/meta-data/", "file:///etc/passwd", "https://example.com/", "http://user:pw@127.0.0.1/"]
)
def test_workbench_blocks_ssrf_targets(api, url):
    r = api.post(f"{api.base}/api/workbench/execute", json={"method": "GET", "url": url}, timeout=15)
    assert r.status_code == 400 and r.json()["detail"]["kind"] == "blocked"


def test_workbench_timeout_and_connection_errors(api):
    slow = api.post(
        f"{api.base}/api/workbench/execute", json={"method": "GET", "url": f"{api.base}/demo/slow?ms=2500", "timeout": 0.5}, timeout=15
    )
    assert slow.status_code == 504 and slow.json()["detail"]["kind"] == "timeout"
    refused = api.post(f"{api.base}/api/workbench/execute", json={"method": "GET", "url": "http://127.0.0.1:9/"}, timeout=15)
    assert refused.status_code == 502 and refused.json()["detail"]["kind"] == "connection"


def test_workbench_rejects_bad_input(api):
    assert api.post(f"{api.base}/api/workbench/execute", json={"method": "TRACE", "url": f"{api.base}/"}, timeout=10).status_code == 422
    bad_header = api.post(
        f"{api.base}/api/workbench/execute",
        json={"method": "GET", "url": f"{api.base}/demo/health", "headers": {"Bad Header": "x"}},
        timeout=10,
    )
    assert bad_header.status_code == 400
    crlf = api.post(
        f"{api.base}/api/workbench/execute",
        json={"method": "GET", "url": f"{api.base}/demo/health", "headers": {"X-A": "a\r\nInjected: 1"}},
        timeout=10,
    )
    assert crlf.status_code == 400
    assert (
        api.post(
            f"{api.base}/api/workbench/execute", json={"method": "GET", "url": f"{api.base}/demo/health", "timeout": 999}, timeout=10
        ).status_code
        == 422
    )


def test_saved_request_lifecycle_and_secret_handling(api, demo_token):
    payload = {
        "name": "protected demo",
        "method": "GET",
        "url": f"{api.base}/demo/protected",
        "auth": {"type": "bearer", "secret": demo_token},
    }
    created = api.post(f"{api.base}/api/requests", json=payload, timeout=10)
    assert created.status_code == 201
    saved = created.json()
    assert saved["auth"]["has_secret"] is True and demo_token not in json.dumps(saved)
    assert demo_token not in api.get(f"{api.base}/api/requests", timeout=10).text

    from app.db import database, models  # same process as the live server

    with database.session_scope() as db:
        stored = db.get(models.SavedRequest, saved["id"]).auth_secret_enc
    assert stored and demo_token not in stored  # encrypted at rest

    assert api.post(f"{api.base}/api/requests/{saved['id']}/execute", timeout=15).json()["status_code"] == 200

    update = {**payload, "name": "renamed", "auth": {"type": "bearer"}}  # blank secret keeps stored one
    assert api.put(f"{api.base}/api/requests/{saved['id']}", json=update, timeout=10).json()["auth"]["has_secret"] is True
    assert api.post(f"{api.base}/api/requests/{saved['id']}/execute", timeout=15).json()["status_code"] == 200

    assert api.delete(f"{api.base}/api/requests/{saved['id']}", timeout=10).status_code == 204
    assert api.get(f"{api.base}/api/requests/{saved['id']}", timeout=10).status_code == 404


# ------------------------------------------------- suites, runs, reports, AI
@pytest.fixture(scope="module")
def smoke_run(api):
    r = api.post(f"{api.base}/api/runs", json={"suite": "smoke"}, timeout=15)
    assert r.status_code == 202, r.text
    return wait_for_run(api, r.json()["id"])


def test_suite_run_executes_and_persists_results(smoke_run):
    assert smoke_run["status"] == "passed", smoke_run
    assert smoke_run["total"] >= 7 and smoke_run["failed"] == 0 and smoke_run["duration_s"] > 0
    assert all(r["outcome"] == "passed" for r in smoke_run["results"])


def test_unknown_or_malicious_suite_rejected(api):
    for suite in ("nope", "smoke; rm -rf /", "../tests", "tests/dashboard"):
        assert api.post(f"{api.base}/api/runs", json={"suite": suite}, timeout=10).status_code == 422


def test_run_blocked_for_disallowed_environment(api):
    env = api.post(f"{api.base}/api/environments", json={"name": "public-site", "base_url": "https://example.com"}, timeout=10).json()
    r = api.post(f"{api.base}/api/runs", json={"suite": "smoke", "environment_id": env["id"]}, timeout=10)
    assert r.status_code == 400
    api.delete(f"{api.base}/api/environments/{env['id']}", timeout=10)


def test_run_listing_filters_sorting_and_validation(api, smoke_run):
    base = f"{api.base}/api/runs"
    data = api.get(base, params={"status": "passed", "q": "smoke", "sort": "duration", "order": "asc"}, timeout=10).json()
    assert data["total"] >= 1 and all(r["status"] == "passed" for r in data["items"])
    assert api.get(base, params={"sort": "bogus"}, timeout=10).status_code == 422
    assert api.get(base, params={"date_from": "31-12-2026"}, timeout=10).status_code == 422
    assert api.get(base, params={"date_from": "2000-01-01", "date_to": "2999-01-01"}, timeout=10).json()["total"] >= 1
    assert api.get(base, params={"date_from": "2999-01-01"}, timeout=10).json()["total"] == 0
    assert api.get(f"{base}/999999", timeout=10).status_code == 404


def test_reports_html_json_csv(api, smoke_run):
    rid = smoke_run["id"]
    html = api.get(f"{api.base}/api/reports/runs/{rid}?format=html", timeout=10)
    assert "attachment" in html.headers["Content-Disposition"] and f"Test run #{rid}" in html.text
    data = api.get(f"{api.base}/api/reports/runs/{rid}?format=json", timeout=10).json()
    assert data["run"]["id"] == rid and len(data["results"]) == smoke_run["total"] and data["latency"]["n"] >= 7
    csv_text = api.get(f"{api.base}/api/reports/runs/{rid}?format=csv", timeout=10).text
    assert csv_text.splitlines()[0] == "nodeid,name,outcome,duration_s,message"
    assert "run_id,suite" not in csv_text
    assert api.get(f"{api.base}/api/reports/history?format=csv", timeout=10).text.startswith("id,suite,status")
    assert api.get(f"{api.base}/api/reports/runs/{rid}?format=exe", timeout=10).status_code == 422
    assert api.get(f"{api.base}/api/reports/runs/999999", timeout=10).status_code == 404


def test_analytics_summary_and_compare(api, smoke_run):
    summary = api.get(f"{api.base}/api/analytics/summary?days=7", timeout=10).json()
    assert summary["has_data"] and summary["totals"]["runs"] >= 1 and len(summary["daily"]) == 7
    lat = summary["latency_all_tests"]
    assert lat["n"] >= 7 and lat["p50"] is not None and lat["p99"] is None  # not enough samples for p99 yet
    cmp_ = api.get(f"{api.base}/api/analytics/compare", params={"a": smoke_run["id"], "b": smoke_run["id"]}, timeout=10).json()
    assert cmp_["diff"]["newly_failing"] == [] and cmp_["diff"]["common_tests"] == smoke_run["total"]
    assert api.get(f"{api.base}/api/analytics/compare", params={"a": 1, "b": 999999}, timeout=10).status_code == 404
    assert api.get(f"{api.base}/api/analytics/summary?days=0", timeout=10).status_code == 422


def test_failed_run_explained_and_history_saved(api):
    run = wait_for_run(api, api.post(f"{api.base}/api/runs", json={"suite": "demo_failures"}, timeout=15).json()["id"])
    assert run["status"] == "failed" and run["failed"] >= 3 and run["passed"] >= 1
    failing = next(r for r in run["results"] if r["nodeid"].endswith("test_expected_ok_but_server_returns_500"))
    assert "500" in failing["message"]

    explained = api.post(f"{api.base}/api/ai/explain-failure", json={"result_id": failing["id"]}, timeout=15).json()["output"]
    assert explained["provider"] == "rules" and explained["llm_used"] is False
    assert any("5xx" in h["text"] for h in explained["hypotheses"]) and explained["facts"]

    passing = next(r for r in run["results"] if r["outcome"] == "passed")
    assert api.post(f"{api.base}/api/ai/explain-failure", json={"result_id": passing["id"]}, timeout=10).status_code == 409
    assert api.post(f"{api.base}/api/ai/explain-failure", json={"result_id": 999999}, timeout=10).status_code == 404

    missing = api.post(f"{api.base}/api/ai/suggest-missing", json={"run_id": run["id"]}, timeout=10).json()["output"]
    assert missing["tests_examined"] == run["total"] and missing["missing"]
    assert api.post(f"{api.base}/api/ai/suggest-missing", json={}, timeout=10).status_code == 422

    history = api.get(f"{api.base}/api/ai/history", timeout=10).json()
    assert {"explain_failure", "suggest_missing"} <= {h["kind"] for h in history}


def test_flaky_detection_across_repeated_runs(api):
    for _ in range(4):
        wait_for_run(api, api.post(f"{api.base}/api/runs", json={"suite": "demo_failures"}, timeout=15).json()["id"])
    flaky = api.get(f"{api.base}/api/analytics/flaky", timeout=10).json()["flaky_tests"]
    assert any(f["nodeid"].endswith("test_intermittent_failure_every_third_run") for f in flaky), flaky
    analysis = api.post(f"{api.base}/api/ai/flaky-analysis", json={"last_runs": 30}, timeout=10).json()["output"]
    assert analysis["flaky_tests"] and analysis["provider"] == "rules"


def test_ai_generate_tests_returns_draft_text_only(api):
    req = "POST /users requires name (string, at most 50 characters) and age between 0 and 120. Returns 201."
    out = api.post(f"{api.base}/api/ai/generate-tests", json={"requirement": req}, timeout=10).json()["output"]
    assert len(out["cases"]) >= 8 and "def test_generated" in out["pytest_draft"]
    assert api.post(f"{api.base}/api/ai/generate-tests", json={"requirement": "short"}, timeout=10).status_code == 422


def test_run_deletion_rules(api, smoke_run):
    assert api.delete(f"{api.base}/api/runs/{smoke_run['id']}", timeout=10).status_code == 204
    assert api.get(f"{api.base}/api/runs/{smoke_run['id']}", timeout=10).status_code == 404


# --------------------------------------------------------------- performance
def test_perf_run_metrics_and_limits(api):
    r = api.post(
        f"{api.base}/api/perf/run",
        json={"method": "GET", "url": f"{api.base}/demo/health", "total_requests": 40, "concurrency": 5},
        timeout=60,
    )
    assert r.status_code == 201, r.text
    perf = r.json()
    assert perf["completed"] == 40 and perf["failed"] == 0 and perf["error_rate"] == 0 and perf["throughput_rps"] > 0
    lat = perf["metrics"]["latency_ms"]
    assert lat["n"] == 40 and lat["p50"] is not None and lat["p95"] is not None and lat["p99"] is None
    assert perf["metrics"]["status_counts"] == {"200": 40}
    assert api.get(f"{api.base}/api/perf/runs/{perf['id']}", timeout=10).json()["id"] == perf["id"]


def test_perf_enforces_limits_and_blocks_external(api):
    over = api.post(f"{api.base}/api/perf/run", json={"url": f"{api.base}/demo/health", "total_requests": 5000}, timeout=10)
    assert over.status_code == 422
    over2 = api.post(f"{api.base}/api/perf/run", json={"url": f"{api.base}/demo/health", "concurrency": 50}, timeout=10)
    assert over2.status_code == 422
    ext = api.post(f"{api.base}/api/perf/run", json={"url": "https://example.com/", "authorized_external": True}, timeout=10)
    assert ext.status_code == 400


def test_perf_counts_errors(api):
    perf = api.post(
        f"{api.base}/api/perf/run", json={"url": f"{api.base}/demo/status/500", "total_requests": 10, "concurrency": 2}, timeout=30
    ).json()
    assert perf["failed"] == 10 and perf["error_rate"] == 100.0
