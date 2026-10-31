"""FastAPI application factory for the API Testing Dashboard."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import ai, analytics, auth, demo, environments, meta, perf, reports, runs, workbench
from app.config import get_settings
from app.core.security import LoginRateLimiter, hash_password, load_or_create_secret_key
from app.core.test_runner import RunManager
from app.db import database
from app.db import repository as repo

log = logging.getLogger("apitd")
STATIC_DIR = Path(__file__).resolve().parent / "static"

CSP = (
    "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _ensure_admin(settings) -> None:
    """Create the dashboard admin on first start. A random password is generated if none is configured."""
    from app.db.database import session_scope

    with session_scope() as db:
        existing = repo.get_user(db, settings.admin_username)
        if existing:
            existing.role = "admin"
            repo.migrate_legacy_ownership(db, settings.admin_username)
            return
        password = settings.admin_password
        generated = not password
        if generated:
            import secrets

            password = secrets.token_urlsafe(12)
        repo.create_user(db, settings.admin_username, hash_password(password), role="admin")
        repo.migrate_legacy_ownership(db, settings.admin_username)
    if generated:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        cred_file = settings.data_dir / "initial_admin_credentials.txt"
        cred_file.write_text(f"username: {settings.admin_username}\npassword: {password}\n", encoding="utf-8")
        print("\n" + "=" * 64)
        print(" First start: dashboard admin account created.")
        print(f"   username: {settings.admin_username}")
        print(f"   password: {password}")
        print(f" (also saved to {cred_file}; delete that file after noting it)")
        print("=" * 64 + "\n", flush=True)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.init_db(settings.database_url)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        app.state.secret_key = load_or_create_secret_key(settings.secret_key, settings.data_dir)
        _ensure_admin(settings)
        with database.session_scope() as db:
            migrated_headers = repo.encrypt_legacy_environment_headers(db, app.state.secret_key)
            if migrated_headers:
                log.info("Encrypted sensitive headers in %d existing environment(s).", migrated_headers)
            interrupted = repo.mark_interrupted_runs(db)
        if interrupted:
            log.warning("Marked %d interrupted run(s) as errored.", interrupted)
        app.state.run_manager = RunManager(settings)
        yield
        app.state.run_manager.shutdown()

    app = FastAPI(title="API Testing Dashboard", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.login_limiter = LoginRateLimiter(settings.login_max_attempts, settings.login_window_seconds)
    app.state.registration_limiter = LoginRateLimiter(settings.registration_max_attempts, settings.registration_window_seconds)
    app.state.run_limiter = LoginRateLimiter(settings.run_max_attempts, settings.run_window_seconds)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    for module in (meta, auth, environments, workbench, runs, reports, analytics, perf, ai):
        app.include_router(module.router)
    if settings.enable_demo_api:
        app.include_router(demo.router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
