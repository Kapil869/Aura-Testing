import xml.etree.ElementTree as ET  # noqa: F401

import pytest

from app.core.test_runner import SUITES, build_command, build_env, parse_junit, summarize
from app.services import ai_service as ai
from app.services import analytics_service as an
from app.services import report_service as rs

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="tests.api.test_a" name="test_ok" time="0.010"/>
<testcase classname="tests.api.test_a" name="test_bad" time="0.200"><failure message="assert 500 == 200 Authorization: Bearer abc123">trace token=SECRET</failure></testcase>
<testcase classname="tests.api.test_a" name="test_err" time="0.0"><error message="boom">tb</error></testcase>
<testcase classname="tests.api.test_a" name="test_skip" time="0.0"><skipped message="why"/></testcase>
</testsuite></testsuites>"""


# ---------------------------------------------------------------- analytics
def test_percentiles_and_insufficient_data():
    assert an.percentile([1, 2, 3, 4, 5], 50) == 3
    assert an.percentile([10, 20], 50) == 15
    assert an.percentile([7], 99) == 7
    few = an.latency_stats([0.1, 0.2, 0.3])
    assert few["n"] == 3 and few["p50"] is None and few["p95"] is None and few["notes"]
    some = an.latency_stats(range(1, 31))
    assert some["p50"] == pytest.approx(15.5) and some["p95"] is not None and some["p99"] is None
    many = an.latency_stats(range(1, 201))
    assert many["p99"] == pytest.approx(198.01)
    assert an.latency_stats([])["n"] == 0


def test_pass_rate_and_summary():
    assert an.pass_rate(0, 0) is None
    assert an.pass_rate(3, 4) == 75.0
    runs = [
        {"status": "passed", "total": 4, "passed": 4, "duration_s": 2.0},
        {"status": "failed", "total": 4, "passed": 2, "duration_s": 4.0},
    ]
    s = an.summarize_runs(runs)
    assert s["runs"] == 2 and s["run_pass_rate"] == 50.0 and s["test_pass_rate"] == 75.0 and s["avg_duration_s"] == 3.0


def test_daily_series_fills_empty_days():
    from datetime import date

    rows = an.daily_series([{"created_at": "2026-01-03T10:00:00Z", "passed": 3, "failed": 1, "errors": 0}], 3, today=date(2026, 1, 3))
    assert [r["date"] for r in rows] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert rows[0]["runs"] == 0 and rows[0]["pass_rate"] is None and rows[2]["pass_rate"] == 75.0


def test_compare_runs():
    a = [
        {"nodeid": "t1", "outcome": "passed", "duration_s": 0.1},
        {"nodeid": "t2", "outcome": "failed", "duration_s": 0.1},
        {"nodeid": "t3", "outcome": "passed", "duration_s": 0.1},
        {"nodeid": "gone", "outcome": "passed", "duration_s": 0.1},
    ]
    b = [
        {"nodeid": "t1", "outcome": "failed", "duration_s": 0.1},
        {"nodeid": "t2", "outcome": "passed", "duration_s": 0.1},
        {"nodeid": "t3", "outcome": "passed", "duration_s": 0.5},
        {"nodeid": "new", "outcome": "passed", "duration_s": 0.1},
    ]
    d = an.compare_runs(a, b)
    assert d["newly_failing"] == ["t1"] and d["newly_passing"] == ["t2"]
    assert d["slower"][0]["nodeid"] == "t3" and d["only_in_a"] == ["gone"] and d["only_in_b"] == ["new"]


def test_detect_flaky():
    hist = {
        "flaky": ["passed", "failed", "passed", "passed", "failed"],
        "always_bad": ["failed"] * 5,
        "good": ["passed"] * 5,
        "short": ["passed", "failed"],
        "regressed": ["passed"] * 4 + ["failed"],
    }
    names = [f["nodeid"] for f in an.detect_flaky(hist)]
    assert names == ["flaky"]  # a single pass->fail switch ('regressed') is a regression, not flakiness


# ------------------------------------------------------------------ runner
def test_parse_junit_outcomes_and_redaction(tmp_path):
    f = tmp_path / "junit.xml"
    f.write_text(JUNIT, encoding="utf-8")
    rows = parse_junit(f)
    assert [r["outcome"] for r in rows] == ["passed", "failed", "error", "skipped"]
    assert rows[1]["nodeid"] == "tests.api.test_a::test_bad"
    assert "abc123" not in rows[1]["message"] and "SECRET" not in rows[1]["details"]
    assert summarize(rows) == {"total": 4, "passed": 1, "failed": 1, "errors": 1, "skipped": 1}


def test_only_allowlisted_suites_build_commands(tmp_path):
    with pytest.raises(KeyError):
        build_command("rm -rf /", tmp_path / "j.xml", None)
    with pytest.raises(KeyError):
        build_command("../../etc/passwd", tmp_path / "j.xml", None)
    cmd = build_command("smoke", tmp_path / "j.xml", None)
    assert cmd[1:3] == ["-m", "pytest"] and "tests/api/test_smoke.py" in cmd
    assert all(isinstance(c, str) for c in cmd)
    assert build_env("http://x", suite_key="demo_failures")["RUN_DEMO_FAILURES"] == "1"
    assert "RUN_DEMO_FAILURES" not in build_env("http://x", suite_key="smoke")
    assert set(SUITES) >= {"smoke", "full_api", "demo_failures"}


def test_runner_environment_is_minimal(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "leak-me")
    monkeypatch.setenv("AI_API_KEY", "sk-leak")
    env = build_env("http://127.0.0.1:1")
    assert env["TARGET_BASE_URL"] == "http://127.0.0.1:1"
    assert "ADMIN_PASSWORD" not in env and "AI_API_KEY" not in env


# ------------------------------------------------------------------ reports
RUN = {
    "id": 7,
    "suite": "<b>smoke</b>",
    "status": "failed",
    "started_at": "t",
    "duration_s": 1.5,
    "target_url": "http://x",
    "total": 2,
    "passed": 1,
    "failed": 1,
    "errors": 0,
    "skipped": 0,
    "message": "",
    "output_tail": "Authorization: Bearer topsecret",
}
RESULTS = [
    {"nodeid": "a::ok", "name": "ok", "outcome": "passed", "duration_s": 0.1, "message": "", "details": ""},
    {
        "nodeid": "a::=cmd()",
        "name": "=cmd()",
        "outcome": "failed",
        "duration_s": 0.2,
        "message": "<script>alert(1)</script> token=abc",
        "details": "",
    },
]


def test_html_report_escapes_and_redacts():
    html = rs.to_html(rs.run_report_data(RUN, RESULTS))
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "&lt;b&gt;smoke" in html and "token=abc" not in html


def test_json_report_redacts_output():
    data = rs.run_report_data(RUN, RESULTS)
    assert "topsecret" not in rs.to_json(data)
    assert data["latency"]["n"] == 2


def test_csv_neutralises_formula_injection():
    out = rs.results_to_csv(RESULTS)
    assert "'=cmd()" in out and out.splitlines()[0].startswith("nodeid,name")
    assert rs.csv_safe("@SUM(1)") == "'@SUM(1)" and rs.csv_safe("plain") == "plain" and rs.csv_safe(5) == 5


def test_history_csv_has_header_and_rows():
    out = rs.history_to_csv([{**RUN, "created_at": "2026-01-01T00:00:00Z"}])
    assert out.splitlines()[0].startswith("id,suite,status")
    assert len(out.splitlines()) == 2


# ----------------------------------------------------------------------- AI
def test_sanitize_for_llm_strips_secrets_and_pii():
    out = ai.sanitize_for_llm("mail bob@corp.com token=abc Authorization: Bearer xyz " + "A" * 40)
    assert "bob@corp.com" not in out and "abc" not in out and "xyz" not in out and "A" * 40 not in out


@pytest.mark.parametrize(
    "message,needle",
    [
        ("assert 500 == 200", "5xx"),
        ("assert 404 == 200", "path"),
        ("assert 401 == 200", "Credentials"),
        ("ReadTimeout: timed out", "slower than"),
        ("ConnectionError: Max retries exceeded", "not running"),
        ("jsonschema.exceptions.ValidationError: 'id' is a required property", "schema"),
    ],
)
def test_explain_failure_rules_matches_patterns(message, needle):
    out = ai.explain_failure_rules({"test": "t", "outcome": "failed", "duration_s": 0.1, "message": message, "details": ""})
    assert any(needle.lower() in h["text"].lower() for h in out["hypotheses"])
    assert all(h["source"] == "rules" for h in out["hypotheses"])
    assert out["facts"] and out["next_steps"]


def test_explain_failure_uses_history_for_flakiness():
    out = ai.explain_failure_rules(
        {"test": "t", "outcome": "failed", "message": "assert 503 == 200", "history": ["passed", "failed", "passed", "failed"]}
    )
    assert any("flakiness" in h["text"].lower() for h in out["hypotheses"])
    out2 = ai.explain_failure_rules({"test": "t", "outcome": "failed", "message": "x", "history": ["failed"] * 4})
    assert any("regression" in h["text"].lower() for h in out2["hypotheses"])


def test_generate_tests_rules_from_requirement():
    req = (
        "POST /users creates a user. name is required and must be a string at most 50 characters. "
        "email is required and must be an email. age must be between 0 and 120. Returns 201. Requires an auth token."
    )
    res = ai.generate_tests_rules(req)
    titles = [c["title"] for c in res["cases"]]
    assert res["cases"][0]["method"] == "POST" and res["cases"][0]["path"] == "/users" and res["cases"][0]["expected_status"] == [201]
    assert any("missing required field 'name'" in t for t in titles)
    assert any("above maximum (121)" in t for t in titles) and any("below minimum (-1)" in t for t in titles)
    assert any("over max length (51)" in t for t in titles)
    assert any(c["category"] == "auth" for c in res["cases"])
    draft = ai.render_pytest_draft(res["cases"])
    compile(draft, "<draft>", "exec")  # syntactically valid Python; never executed
    assert "def test_generated" in draft


def test_generate_tests_without_method_uses_assumption():
    res = ai.generate_tests_rules("Something vague about widgets")
    assert res["assumptions"] and res["cases"]


def test_pytest_draft_escapes_hostile_strings():
    cases = [
        {
            "title": "x'); __import__('os').system('echo hi'); ('",
            "category": "negative",
            "method": "GET",
            "path": "/",
            "body": None,
            "raw_body": None,
            "headers": {},
            "expected_status": [400],
            "note": "",
        }
    ]
    compile(ai.render_pytest_draft(cases), "<draft>", "exec")


def test_clean_cases_drops_unsafe_llm_output():
    cleaned = ai._clean_cases(
        [
            {"method": "TRACE", "path": "/x"},
            {"method": "get", "path": "no-slash"},
            "str",
            {"method": "post", "path": "/ok", "expected_status": [201, "x", 9999], "title": "t"},
        ]
    )
    assert len(cleaned) == 1 and cleaned[0]["expected_status"] == [201]


def test_suggest_missing_detects_gaps():
    res = ai.suggest_missing_rules(["test_create_user", "test_get_user_schema", "test_invalid_email_rejected"])
    missing = {m["category"] for m in res["missing"]}
    assert "negative" in res["covered_categories"] and "schema" in res["covered_categories"]
    assert "boundary" in missing and "authentication" in missing


def test_flaky_analysis_rules():
    class Cfg:  # minimal stand-in; rules path does not touch the network
        ai_provider = "none"
        ai_api_key = ""
        ai_base_url = ""

    res = ai.flaky_analysis(
        Cfg(), {"t::flaky": ["passed", "failed", "passed", "failed"], "t::ok": ["passed"] * 4}, {"t::flaky": "503 timeout"}
    )
    assert [f["nodeid"] for f in res["flaky_tests"]] == ["t::flaky"] and res["flaky_tests"][0]["hypotheses"]
    assert ai.flaky_analysis(Cfg(), {"t::ok": ["passed"] * 4}, {})["summary"].startswith("No flaky")


def test_llm_disabled_without_key():
    class Cfg:
        ai_provider = "openai_compatible"
        ai_api_key = ""
        ai_base_url = "https://api.openai.com/v1"
        ai_model = "m"

    assert not ai.llm_enabled(Cfg())
    assert ai.ai_status(Cfg())["mode"] == "rules"
