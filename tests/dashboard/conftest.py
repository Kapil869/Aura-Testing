"""Fixtures that start the real dashboard (all routes, SQLite temp DB) on a free port."""

from __future__ import annotations

import os

import pytest
import requests

from tests.conftest import free_port, start_uvicorn

ADMIN_USER = "qa-admin"
ADMIN_PASS = "test-only-password-123"
CSRF = {"X-Requested-With": "api-dashboard"}


@pytest.fixture(scope="session")
def live_dashboard(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dashboard")
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{(tmp / 'test.db').as_posix()}",
            "DATA_DIR": str(tmp / "data"),
            "REPORTS_DIR": str(tmp / "reports"),
            "SECRET_KEY": "test-secret-key-not-for-production",
            "ADMIN_USERNAME": ADMIN_USER,
            "ADMIN_PASSWORD": ADMIN_PASS,
            "ENABLE_DEMO_API": "true",
            "ALLOW_LOCAL_TARGETS": "true",
            "ALLOW_EXTERNAL_TARGETS": "false",
            "TEST_TARGET_URL": "",
            "TEST_RETRY_COUNT": "1",
            "TEST_TIMEOUT_SECONDS": "240",
            "AI_PROVIDER": "none",
            "AI_API_KEY": "",
            "LOGIN_MAX_ATTEMPTS": "50",
        }
    )
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    port = free_port()
    server, thread = start_uvicorn(create_app(), port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def anon(live_dashboard):
    """Unauthenticated client (CSRF header only)."""
    s = requests.Session()
    s.headers.update(CSRF)
    s.base = live_dashboard
    return s


@pytest.fixture(scope="session")
def api(live_dashboard):
    """Logged-in client."""
    s = requests.Session()
    s.headers.update(CSRF)
    s.base = live_dashboard
    r = s.post(f"{live_dashboard}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return s
