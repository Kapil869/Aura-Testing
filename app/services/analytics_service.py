"""Pure analytics helpers (no database or web dependencies)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

# Minimum number of samples before a percentile is reported.
MIN_SAMPLES = {"p50": 5, "p95": 20, "p99": 100}


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..100) of an already sorted list."""
    if not sorted_values:
        raise ValueError("no samples")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (q / 100) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


def latency_stats(samples: Iterable[float]) -> dict[str, Any]:
    """Summary statistics; percentiles are None when there are too few samples."""
    values = sorted(float(v) for v in samples if v is not None)
    n = len(values)
    out: dict[str, Any] = {"n": n, "min": None, "max": None, "mean": None, "p50": None, "p95": None, "p99": None, "notes": []}
    if n == 0:
        out["notes"].append("No samples recorded yet.")
        return out
    out.update(min=values[0], max=values[-1], mean=sum(values) / n)
    for label, q in (("p50", 50), ("p95", 95), ("p99", 99)):
        if n >= MIN_SAMPLES[label]:
            out[label] = percentile(values, q)
        else:
            out["notes"].append(f"{label} needs at least {MIN_SAMPLES[label]} samples (have {n}).")
    return out


def pass_rate(passed: int, total: int) -> float | None:
    return None if not total else round(100.0 * passed / total, 1)


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate over finished runs (dicts with total/passed/failed/errors/duration_s)."""
    tests = sum(r["total"] for r in runs)
    passed = sum(r["passed"] for r in runs)
    green = sum(1 for r in runs if r["status"] == "passed")
    durations = [r["duration_s"] for r in runs if r.get("duration_s") is not None]
    return {
        "runs": len(runs),
        "green_runs": green,
        "run_pass_rate": pass_rate(green, len(runs)),
        "tests_executed": tests,
        "test_pass_rate": pass_rate(passed, tests),
        "avg_duration_s": round(sum(durations) / len(durations), 2) if durations else None,
    }


def daily_series(runs: list[dict[str, Any]], days: int, today: date | None = None) -> list[dict[str, Any]]:
    """One row per calendar day (UTC) for the last ``days`` days, including empty days."""
    today = today or date.today()
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "passed": 0, "failed": 0})
    for r in runs:
        day = r["created_at"][:10]
        b = buckets[day]
        b["runs"] += 1
        b["passed"] += r["passed"]
        b["failed"] += r["failed"] + r["errors"]
    rows = []
    for offset in range(days - 1, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        b = buckets.get(d, {"runs": 0, "passed": 0, "failed": 0})
        rows.append({"date": d, **b, "pass_rate": pass_rate(b["passed"], b["passed"] + b["failed"])})
    return rows


def compare_runs(a: list[dict[str, Any]], b: list[dict[str, Any]], slower_factor: float = 1.5, min_delta_s: float = 0.05) -> dict[str, Any]:
    """Compare per-test results of run A (baseline) with run B (candidate)."""
    ma = {r["nodeid"]: r for r in a}
    mb = {r["nodeid"]: r for r in b}
    bad = {"failed", "error"}
    newly_failing, newly_passing, still_failing, slower, only_a, only_b = [], [], [], [], [], []
    for node, rb in mb.items():
        ra = ma.get(node)
        if ra is None:
            only_b.append(node)
            continue
        a_bad, b_bad = ra["outcome"] in bad, rb["outcome"] in bad
        if not a_bad and b_bad:
            newly_failing.append(node)
        elif a_bad and rb["outcome"] == "passed":
            newly_passing.append(node)
        elif a_bad and b_bad:
            still_failing.append(node)
        da, db_ = ra.get("duration_s") or 0.0, rb.get("duration_s") or 0.0
        if da > 0 and db_ > da * slower_factor and (db_ - da) >= min_delta_s:
            slower.append({"nodeid": node, "before_s": da, "after_s": db_, "factor": round(db_ / da, 2)})
    only_a = [n for n in ma if n not in mb]
    return {
        "newly_failing": sorted(newly_failing),
        "newly_passing": sorted(newly_passing),
        "still_failing": sorted(still_failing),
        "slower": sorted(slower, key=lambda x: -x["factor"]),
        "only_in_a": sorted(only_a),
        "only_in_b": sorted(only_b),
        "common_tests": len(set(ma) & set(mb)),
    }


def detect_flaky(history: dict[str, list[str]], min_runs: int = 4, min_flip_rate: float = 0.2) -> list[dict[str, Any]]:
    """Tests that both passed and failed in recorded history, ranked by outcome flip rate."""
    bad = {"failed", "error"}
    found = []
    for node, outcomes in history.items():
        n = len(outcomes)
        if n < min_runs:
            continue
        fails = sum(1 for o in outcomes if o in bad)
        passes = n - fails
        if fails == 0 or passes == 0:
            continue
        flips = sum(1 for i in range(1, n) if (outcomes[i] in bad) != (outcomes[i - 1] in bad))
        if flips < 2:  # a single pass->fail (or fail->pass) switch is a regression/fix, not flakiness
            continue
        flip_rate = flips / (n - 1)
        if flip_rate >= min_flip_rate:
            found.append(
                {
                    "nodeid": node,
                    "runs": n,
                    "passes": passes,
                    "failures": fails,
                    "flip_rate": round(flip_rate, 2),
                    "failure_rate": round(fails / n, 2),
                    "recent": outcomes[-10:],
                }
            )
    return sorted(found, key=lambda x: (-x["flip_rate"], -x["failure_rate"], x["nodeid"]))
