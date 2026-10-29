"""Tests for demo failure scenarios.

Normal test runs verify the demo API's failure modes. The dashboard's
"Demo: failures and flakiness" suite sets RUN_DEMO_FAILURES=1 to deliberately
make the corresponding assertions incorrect for failure-analysis examples.
"""

import os

import pytest
import requests
from jsonschema import ValidationError

from tests.helpers import assert_matches_schema

_DEMO_FAILURES = os.getenv("RUN_DEMO_FAILURES") == "1"


def test_expected_ok_but_server_returns_500(base_url, http):
    status_code = http.get(f"{base_url}/demo/status/500").status_code
    assert status_code == (200 if _DEMO_FAILURES else 500)


def test_response_does_not_match_user_schema(base_url, http):
    response_data = http.get(f"{base_url}/demo/health").json()
    if _DEMO_FAILURES:
        assert_matches_schema(response_data, "user")
    else:
        with pytest.raises(ValidationError):
            assert_matches_schema(response_data, "user")


def test_client_timeout_not_handled(base_url, http):
    if _DEMO_FAILURES:
        http.get(f"{base_url}/demo/slow", params={"ms": 1200}, timeout=0.3)
    else:
        with pytest.raises(requests.Timeout):
            http.get(f"{base_url}/demo/slow", params={"ms": 1200}, timeout=0.3)


def test_intermittent_failure_every_third_run(base_url, http):
    """Shares one server-side counter, so the outcome varies from run to run."""
    status_code = http.get(f"{base_url}/demo/flaky", params={"key": "dashboard-demo"}).status_code
    if _DEMO_FAILURES:
        assert status_code == 200
    else:
        assert status_code in (200, 503)


def test_this_one_passes(base_url, http):
    assert http.get(f"{base_url}/demo/health").status_code == 200
