"""Application settings, loaded from environment variables / the .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent


def _text(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_text(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_text(name, str(default)))
    except ValueError:
        return default


def _list(name: str) -> tuple[str, ...]:
    return tuple(p.strip().lower() for p in _text(name).split(",") if p.strip())


def _path(name: str, default: str) -> Path:
    raw = Path(_text(name, default))
    return raw if raw.is_absolute() else (ROOT_DIR / raw).resolve()


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    data_dir: Path
    reports_dir: Path
    secret_key: str
    admin_username: str
    admin_password: str
    registration_enabled: bool
    registration_min_password_length: int
    registration_max_attempts: int
    registration_window_seconds: int
    session_hours: int
    cookie_secure: bool
    login_max_attempts: int
    login_window_seconds: int
    enable_demo_api: bool
    demo_api_token: str
    allow_local_targets: bool
    allow_private_targets: bool
    allow_external_targets: bool
    external_allowlist: tuple[str, ...]
    test_target_url: str
    max_concurrent_runs: int
    max_active_runs: int
    run_max_attempts: int
    run_window_seconds: int
    test_timeout_seconds: int
    test_retry_count: int
    perf_enabled: bool
    perf_max_requests: int
    perf_max_concurrency: int
    perf_max_duration_seconds: int
    ai_provider: str
    ai_api_key: str
    ai_base_url: str
    ai_model: str
    ai_timeout_seconds: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT_DIR / ".env", override=False)
    return Settings(
        app_env=_text("APP_ENV", "development"),
        database_url=_text("DATABASE_URL", "sqlite:///./data/api_dashboard.db"),
        data_dir=_path("DATA_DIR", "data"),
        reports_dir=_path("REPORTS_DIR", "reports"),
        secret_key=_text("SECRET_KEY"),
        admin_username=_text("ADMIN_USERNAME", "admin"),
        admin_password=_text("ADMIN_PASSWORD"),
        registration_enabled=_bool("REGISTRATION_ENABLED", True),
        registration_min_password_length=max(12, _int("REGISTRATION_MIN_PASSWORD_LENGTH", 12)),
        registration_max_attempts=max(1, _int("REGISTRATION_MAX_ATTEMPTS", 5)),
        registration_window_seconds=max(60, _int("REGISTRATION_WINDOW_SECONDS", 3600)),
        session_hours=max(1, _int("SESSION_HOURS", 8)),
        cookie_secure=_bool("COOKIE_SECURE", _text("APP_ENV", "development").lower() == "production"),
        login_max_attempts=max(1, _int("LOGIN_MAX_ATTEMPTS", 5)),
        login_window_seconds=max(10, _int("LOGIN_WINDOW_SECONDS", 300)),
        enable_demo_api=_bool("ENABLE_DEMO_API", True),
        demo_api_token=_text("DEMO_API_TOKEN", "demo-token-local"),
        allow_local_targets=_bool("ALLOW_LOCAL_TARGETS", _text("APP_ENV", "development").lower() != "production"),
        allow_private_targets=_bool("ALLOW_PRIVATE_NETWORK_TARGETS", False),
        allow_external_targets=_bool("ALLOW_EXTERNAL_TARGETS", False),
        external_allowlist=_list("EXTERNAL_TARGET_ALLOWLIST"),
        test_target_url=_text("TEST_TARGET_URL").rstrip("/"),
        max_concurrent_runs=max(1, min(4, _int("MAX_CONCURRENT_RUNS", 1))),
        max_active_runs=max(1, min(20, _int("MAX_ACTIVE_RUNS", 5))),
        run_max_attempts=max(1, _int("RUN_MAX_ATTEMPTS", 30)),
        run_window_seconds=max(10, _int("RUN_WINDOW_SECONDS", 60)),
        test_timeout_seconds=max(10, min(1800, _int("TEST_TIMEOUT_SECONDS", 180))),
        test_retry_count=max(0, min(5, _int("TEST_RETRY_COUNT", 2))),
        perf_enabled=_bool("PERF_TESTING_ENABLED", _text("APP_ENV", "development").lower() != "production"),
        perf_max_requests=max(1, min(5000, _int("PERF_MAX_REQUESTS", 500))),
        perf_max_concurrency=max(1, min(50, _int("PERF_MAX_CONCURRENCY", 20))),
        perf_max_duration_seconds=max(5, min(300, _int("PERF_MAX_DURATION_SECONDS", 60))),
        ai_provider=_text("AI_PROVIDER", "none").lower(),
        ai_api_key=_text("AI_API_KEY"),
        ai_base_url=_text("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        ai_model=_text("AI_MODEL", "gpt-4o-mini"),
        ai_timeout_seconds=max(5, min(120, _int("AI_TIMEOUT_SECONDS", 30))),
    )
