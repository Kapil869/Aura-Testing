"""Shared fixtures.

* ``base_url``  - TARGET_BASE_URL if set (the dashboard sets it), otherwise a local
                  deterministic demo server is started so tests work offline.
* ``http``      - a requests.Session with a default timeout.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_uvicorn(app, port: int):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("Test server did not start in time")
    return server, thread


class TimeoutSession(requests.Session):
    def __init__(self, timeout: float):
        super().__init__()
        self._default_timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._default_timeout)
        return super().request(*args, **kwargs)


@pytest.fixture(scope="session")
def base_url():
    url = os.getenv("TARGET_BASE_URL", "").rstrip("/")
    if url:
        yield url
        return
    from fastapi import FastAPI

    from app.api.routes.demo import router

    app = FastAPI()
    app.include_router(router)
    port = free_port()
    server, thread = start_uvicorn(app, port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def request_timeout() -> float:
    return float(os.getenv("TARGET_TIMEOUT", "10"))


@pytest.fixture(scope="session")
def http(request_timeout):
    session = TimeoutSession(request_timeout)
    yield session
    session.close()


@pytest.fixture(scope="session")
def demo_token() -> str:
    return os.getenv("DEMO_API_TOKEN", "demo-token-local")
