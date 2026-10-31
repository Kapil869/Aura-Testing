"""Bounded, allow-listed PyTest execution.

Only suites defined in SUITES can run. Nothing from the UI is ever placed in the
command line: the UI sends a suite *key*, and the target URL is passed to the test
process through the TARGET_BASE_URL environment variable.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

from app.config import ROOT_DIR, Settings
from app.core.security import redact_text
from app.db import repository as repo
from app.db.database import session_scope
from app.db.models import utcnow

SUITES: dict[str, dict[str, Any]] = {
    "smoke": {
        "label": "Smoke tests",
        "paths": ["tests/api/test_smoke.py"],
        "extra": [],
        "description": "Health, headers and basic latency.",
    },
    "users_crud": {
        "label": "Users CRUD + schema",
        "paths": ["tests/api/test_users_crud.py"],
        "extra": [],
        "description": "Positive flows with JSON Schema validation.",
    },
    "negative_boundary": {
        "label": "Negative and boundary",
        "paths": ["tests/api/test_negative_boundary.py"],
        "extra": [],
        "description": "Invalid input, limits and wrong methods.",
    },
    "resilience": {
        "label": "Timeouts and errors",
        "paths": ["tests/api/test_resilience.py"],
        "extra": [],
        "description": "Timeouts, connection errors and auth.",
    },
    "full_api": {"label": "Full API suite", "paths": ["tests/api"], "extra": [], "description": "Everything above in one run."},
    "demo_failures": {
        "label": "Demo: failures and flakiness",
        "paths": ["tests/api/test_demo_failures.py"],
        "extra": [],
        "env": {"RUN_DEMO_FAILURES": "1"},
        "description": "Intentionally failing/flaky tests to demonstrate failure inspection and AI analysis.",
    },
}

_ENV_PASSTHROUGH = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "VIRTUAL_ENV",
    "DEMO_API_TOKEN",
)


def build_command(suite_key: str, junit_path: Path, html_path: Path | None) -> list[str]:
    suite = SUITES[suite_key]  # KeyError for unknown suites -> never builds arbitrary commands
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *suite["paths"],
        *suite["extra"],
        f"--junitxml={junit_path}",
        "-p",
        "no:cacheprovider",
        "-q",
        "--tb=short",
        "--color=no",
    ]
    if html_path is not None:
        cmd += [f"--html={html_path}", "--self-contained-html"]
    return cmd


def build_env(target_url: str, timeout_s: float = 10.0, suite_key: str | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    env.update(SUITES.get(suite_key or "", {}).get("env", {}))
    env.update({"TARGET_BASE_URL": target_url, "TARGET_TIMEOUT": str(timeout_s), "PYTHONPATH": str(ROOT_DIR), "PYTHONIOENCODING": "utf-8"})
    return env


def parse_junit(xml_path: Path) -> list[dict[str, Any]]:
    """Convert a pytest junit XML file (written by our own pytest process) to result rows."""
    rows = []
    for tc in ET.parse(xml_path).getroot().iter("testcase"):
        classname, name = tc.get("classname", ""), tc.get("name", "")
        outcome, message, details = "passed", "", ""
        for tag, label in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            node = tc.find(tag)
            if node is not None:
                outcome, message, details = label, node.get("message", "") or "", node.text or ""
                break
        rows.append(
            {
                "nodeid": f"{classname}::{name}" if classname else name,
                "name": name,
                "classname": classname,
                "outcome": outcome,
                "duration_s": float(tc.get("time", 0) or 0),
                "message": redact_text(message, 2000),
                "details": redact_text(details, 6000),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    count = lambda o: sum(1 for r in rows if r["outcome"] == o)  # noqa: E731
    return {"total": len(rows), "passed": count("passed"), "failed": count("failed"), "errors": count("error"), "skipped": count("skipped")}


def _preflight(target_url: str, retries: int) -> tuple[bool, int, str]:
    """Check the target is reachable, retrying with backoff. Returns (ok, retries_used, message)."""
    last = ""
    for attempt in range(retries + 1):
        try:
            if requests.get(f"{target_url}/demo/health", timeout=5, allow_redirects=False).status_code == 200:
                return True, attempt, ""
            last = "health endpoint did not return 200"
        except requests.RequestException as exc:
            last = type(exc).__name__
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    return False, retries, f"Target {target_url} is not reachable ({last})."


def execute_suite(run_id: int, suite_key: str, target_url: str, settings: Settings) -> None:
    """Worker body: runs in a background thread and records everything in the DB."""
    started = time.perf_counter()
    with session_scope() as db:
        run = repo.get_run_for_worker(db, run_id)
        if run is None:
            return
        run.status, run.started_at = "running", utcnow()

    status, message, tail, rows, retries_used = "error", "", "", [], 0
    try:
        ok, retries_used, msg = _preflight(target_url, settings.test_retry_count)
        if not ok:
            message = msg
        else:
            settings.reports_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="apitd_") as tmp:
                junit = Path(tmp) / "junit.xml"
                html = settings.reports_dir / f"pytest_run_{run_id}.html" if importlib.util.find_spec("pytest_html") else None
                try:
                    proc = subprocess.run(
                        build_command(suite_key, junit, html),
                        cwd=ROOT_DIR,
                        env=build_env(target_url, suite_key=suite_key),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=settings.test_timeout_seconds,
                        shell=False,
                    )
                    tail = redact_text((proc.stdout or "") + "\n" + (proc.stderr or ""))[-6000:]
                    if junit.exists():
                        rows = parse_junit(junit)
                        counts = summarize(rows)
                        bad = counts["failed"] + counts["errors"]
                        status = "passed" if counts["total"] and not bad else "failed"
                        if counts["total"] == 0:
                            status, message = "error", "No tests were collected."
                    else:
                        message = f"PyTest exited with code {proc.returncode} without producing results."
                except subprocess.TimeoutExpired:
                    status, message = "timeout", f"Run exceeded the {settings.test_timeout_seconds}s limit and was stopped."
    except Exception as exc:  # never let a worker die silently
        status, message = "error", f"Runner error: {type(exc).__name__}: {redact_text(exc)}"

    counts = summarize(rows)
    with session_scope() as db:
        run = repo.get_run_for_worker(db, run_id)
        if run is None:
            return
        repo.add_results(db, run_id, rows)
        run.status, run.message, run.output_tail = status, message, tail
        run.total, run.passed, run.failed, run.errors, run.skipped = (counts[k] for k in ("total", "passed", "failed", "errors", "skipped"))
        run.retries_used = retries_used
        run.finished_at = utcnow()
        run.duration_s = round(time.perf_counter() - started, 2)


class RunManager:
    """Bounded thread pool; queue depth is limited by Settings.max_active_runs."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool = ThreadPoolExecutor(max_workers=settings.max_concurrent_runs, thread_name_prefix="suite-runner")

    def submit(self, run_id: int, suite_key: str, target_url: str) -> None:
        self.pool.submit(execute_suite, run_id, suite_key, target_url, self.settings)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
