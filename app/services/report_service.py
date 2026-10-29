"""Build downloadable HTML / JSON / CSV reports. All data is redacted and escaped."""

from __future__ import annotations

import csv
import html
import io
import json
from typing import Any

from app.core.security import redact_text
from app.services.analytics_service import latency_stats

_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> Any:
    """Neutralise spreadsheet formula injection."""
    if isinstance(value, str) and value.startswith(_CSV_DANGEROUS):
        return "'" + value
    return value


def run_report_data(run: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    clean = []
    for r in results:
        clean.append({**r, "message": redact_text(r.get("message", ""), 2000), "details": redact_text(r.get("details", ""), 4000)})
    return {
        "run": {**run, "message": redact_text(run.get("message", "")), "output_tail": redact_text(run.get("output_tail", ""), 6000)},
        "latency": latency_stats(r["duration_s"] for r in results if r["outcome"] != "skipped"),
        "results": clean,
    }


def to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, default=str)


def results_to_csv(results: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["nodeid", "name", "outcome", "duration_s", "message"])
    for r in results:
        writer.writerow(
            [csv_safe(r["nodeid"]), csv_safe(r["name"]), r["outcome"], r["duration_s"], csv_safe(redact_text(r.get("message", ""), 500))]
        )
    return buf.getvalue()


def history_to_csv(runs: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    cols = ["id", "suite", "status", "created_at", "duration_s", "total", "passed", "failed", "errors", "skipped", "target_url"]
    writer.writerow(cols)
    for r in runs:
        writer.writerow([csv_safe(r.get(c)) for c in cols])
    return buf.getvalue()


def _fmt(value: Any, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def to_html(data: dict[str, Any]) -> str:
    run, lat = data["run"], data["latency"]
    esc = html.escape
    rows = []
    for r in data["results"]:
        detail = ""
        if r["outcome"] in ("failed", "error"):
            detail = f"<pre>{esc(r['message'])}\n{esc(r['details'])}</pre>"
        rows.append(
            f"<tr class='{esc(r['outcome'])}'><td>{esc(r['nodeid'])}{detail}</td>"
            f"<td>{esc(r['outcome'])}</td><td>{r['duration_s']:.3f}</td></tr>"
        )
    notes = "".join(f"<li>{esc(n)}</li>" for n in lat["notes"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Test run #{run["id"]} report</title>
<style>
body{{font-family:Segoe UI,system-ui,sans-serif;margin:2rem;color:#1b252c;max-width:960px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ccd5da;padding:.4rem .6rem;text-align:left;vertical-align:top}}
tr.passed td:nth-child(2){{color:#117a4d}} tr.failed td:nth-child(2),tr.error td:nth-child(2){{color:#b3261e}}
pre{{white-space:pre-wrap;background:#f3f6f8;padding:.5rem;margin:.4rem 0 0}}
.meta span{{display:inline-block;margin-right:1.2rem}}
</style></head><body>
<h1>Test run #{run["id"]} &mdash; {esc(str(run["suite"]))}</h1>
<p class="meta"><span>Status: <b>{esc(str(run["status"]))}</b></span><span>Started: {esc(str(run.get("started_at")))}</span>
<span>Duration: {_fmt(run.get("duration_s"), 2)} s</span><span>Target: {esc(str(run.get("target_url")))}</span></p>
<p class="meta"><span>Total: {run["total"]}</span><span>Passed: {run["passed"]}</span><span>Failed: {run["failed"]}</span>
<span>Errors: {run["errors"]}</span><span>Skipped: {run["skipped"]}</span></p>
<h2>Test duration (seconds)</h2>
<p>n={lat["n"]} &middot; mean {_fmt(lat["mean"])} &middot; p50 {_fmt(lat["p50"])} &middot; p95 {_fmt(lat["p95"])} &middot; p99 {_fmt(lat["p99"])}</p>
<ul>{notes}</ul>
<h2>Results</h2>
<table><tr><th>Test</th><th>Outcome</th><th>Seconds</th></tr>{"".join(rows)}</table>
<p><small>Secrets are redacted from this report.</small></p>
</body></html>"""
