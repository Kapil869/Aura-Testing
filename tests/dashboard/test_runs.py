"""Test-run ownership and report access regression coverage."""

import time
from uuid import uuid4

from tests.dashboard.test_multi_user import _register


def _wait(client, base, run_id):
    for _ in range(120):
        response = client.get(f"{base}/api/runs/{run_id}", timeout=10)
        if response.status_code == 200 and response.json()["status"] not in ("queued", "running"):
            return
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_run_history_and_reports_are_owner_scoped(live_dashboard):
    password = "run-owner-password-123"
    owner = _register(live_dashboard, f"run-owner-{uuid4().hex[:8]}", password)
    other = _register(live_dashboard, f"run-other-{uuid4().hex[:8]}", password)
    started = owner.post(f"{live_dashboard}/api/runs", json={"suite": "smoke"}, timeout=10)
    assert started.status_code == 202
    run_id = started.json()["id"]
    _wait(owner, live_dashboard, run_id)
    assert other.get(f"{live_dashboard}/api/runs/{run_id}", timeout=10).status_code == 404
    assert other.get(f"{live_dashboard}/api/reports/runs/{run_id}", timeout=10).status_code == 404
    assert all(item["id"] != run_id for item in other.get(f"{live_dashboard}/api/runs", timeout=10).json()["items"])
