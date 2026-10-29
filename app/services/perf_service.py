"""Bounded concurrent load generator. Safe limits come from Settings."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from app.core.security import UrlBlocked, UrlPolicy, evaluate_url
from app.services.analytics_service import latency_stats
from app.services.http_client import RequestFailed, clamp_timeout, send_request

_perf_lock = threading.Lock()  # only one load test at a time


class PerfBusy(Exception):
    pass


def run_load_test(
    *,
    method: str,
    url: str,
    total_requests: int,
    concurrency: int,
    timeout: float,
    max_duration_s: int,
    policy: UrlPolicy,
    authorized_external: bool = False,
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> dict[str, Any]:
    if not _perf_lock.acquire(blocking=False):
        raise PerfBusy("Another performance test is already running.")
    try:
        evaluate_url(url, policy, authorized_external)  # validate once up-front (raises UrlBlocked)
        timeout = clamp_timeout(timeout)
        deadline = time.perf_counter() + max_duration_s
        latencies: list[float] = []
        status_counts: dict[str, int] = {}
        error_kinds: dict[str, int] = {}
        counters = {"ok": 0, "failed": 0, "done": 0}
        lock = threading.Lock()
        thread_local = threading.local()

        def one(_idx: int) -> None:
            if time.perf_counter() > deadline:
                return
            session = getattr(thread_local, "session", None)
            if session is None:
                session = thread_local.session = requests.Session()
            try:
                res = send_request(
                    method=method,
                    url=url,
                    headers=headers,
                    body=body,
                    timeout=timeout,
                    policy=policy,
                    authorized_external=authorized_external,
                    session=session,
                    revalidate=False,
                )
                code = res["status_code"]
                with lock:
                    counters["done"] += 1
                    latencies.append(res["duration_ms"])
                    status_counts[str(code)] = status_counts.get(str(code), 0) + 1
                    counters["ok" if code < 400 else "failed"] += 1
            except RequestFailed as exc:
                with lock:
                    counters["done"] += 1
                    counters["failed"] += 1
                    error_kinds[exc.kind] = error_kinds.get(exc.kind, 0) + 1
            except (UrlBlocked, ValueError):
                with lock:
                    counters["done"] += 1
                    counters["failed"] += 1
                    error_kinds["blocked_or_invalid"] = error_kinds.get("blocked_or_invalid", 0) + 1

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(one, range(total_requests)))
        elapsed = max(time.perf_counter() - started, 1e-9)
        done = counters["done"]
        stats = latency_stats(latencies)
        stopped_early = done < total_requests
        return {
            "status": "stopped_early" if stopped_early else "completed",
            "duration_s": round(elapsed, 3),
            "requested": total_requests,
            "completed": done,
            "succeeded": counters["ok"],
            "failed": counters["failed"],
            "throughput_rps": round(done / elapsed, 2),
            "error_rate": round(100.0 * counters["failed"] / done, 2) if done else 0.0,
            "latency_ms": stats,
            "status_counts": status_counts,
            "error_kinds": error_kinds,
            "note": f"Stopped at the {max_duration_s}s safety limit." if stopped_early else "",
        }
    finally:
        _perf_lock.release()
