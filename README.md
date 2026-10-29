# API Testing Dashboard

A self-hosted QA automation platform: run PyTest API suites from a dark web dashboard, send ad-hoc requests, load-test locally, track pass rates and latency percentiles, detect flaky tests, and get AI-assisted (or rules-based) failure analysis. Everything works offline against a bundled deterministic demo API, with SQLite and no database server.

> Read [BUILD_STATUS.md](BUILD_STATUS.md) for exactly what was verified when this project was generated and what still needs a first run on your machine.

## Features

| Area | What you get |
|---|---|
| Dashboard | Run ticker tape, stats, charts (Chart.js), searchable/sortable/filterable run history, date filters, run detail with per-test results, loading/empty/error states |
| Request workbench | GET/POST/PUT/PATCH/DELETE (+HEAD/OPTIONS), params, headers, bearer/basic/API-key auth, JSON body, timeouts, saved requests (credentials encrypted at rest) |
| Automation framework | Requests + PyTest, fixtures, parametrized positive/negative/boundary tests, JSON Schema, status/body/header/timing assertions, timeouts, retries |
| Execution engine | Allow-listed suites only, bounded thread pool, queue limit, per-run timeout, preflight retries, history and failure details in SQLite |
| Reports and analytics | HTML/JSON/CSV downloads, run comparison, p50/p95/p99 (shown only with enough samples), flaky detection |
| Performance | Bounded concurrent load tests with throughput, error rate, latency percentiles, hard safety limits |
| Security | Login, scrypt password hashing, signed HttpOnly session cookie, CSRF header, login rate limit, SSRF policy, secret redaction, no arbitrary command execution |
| AI-assisted QA | Failure explanation (facts vs hypotheses), test-case drafts from plain English, missing-coverage hints, flaky analysis, saved history; rules-based fallback with no API key |

## Quick start on Windows (VS Code)

Prerequisite: Python 3.11 or newer (from python.org, "Add to PATH" ticked).

1. Unzip `api-testing-dashboard.zip`, then open the folder in VS Code (**File > Open Folder**).
2. Open a terminal (**Terminal > New Terminal**, Command Prompt or PowerShell) and run:
   ```
   scripts\setup_windows.bat
   scripts\run_dashboard.bat
   ```
   Manual equivalent:
   ```
   py -3 -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
3. Browse to <http://127.0.0.1:8000>.
4. **First login:** with `ADMIN_PASSWORD` blank (the default) a random password is generated on first start. It is printed in the terminal and saved in `data/initial_admin_credentials.txt`. The username is `admin`. Delete that file once you have noted the password. To choose your own, set `ADMIN_PASSWORD` in `.env` **before** the first start (or delete `data/api_dashboard.db` to recreate the user).
5. Go to **Test runs**, choose *Smoke tests*, press **Run suite**, and click the finished run. Then try *Demo: failures and flakiness* a few times (the flaky test fails every third run) and use **Explain this failure** and the **Analytics** page.

macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && scripts/run_dashboard.sh`.

## Running the automated tests

```
.venv\Scripts\activate
python -m ruff check .
python -m pytest
```
`scripts\run_tests.bat` does both and writes an HTML report to `reports\local-report.html`.

* `tests/api/` is the API automation framework. It runs against `TARGET_BASE_URL` if set, otherwise it starts its own local demo server, so it needs no internet.
* `tests/dashboard/` tests the backend (security, SSRF, analytics, reports, AI rules, HTTP client) and runs end-to-end against a real dashboard instance with a temporary database, including executing suites through the dashboard.
* `tests/api/test_demo_failures.py` verifies the demo API's error, timeout, and flaky behavior during normal test runs. The dashboard sets `RUN_DEMO_FAILURES=1` for the *Demo: failures and flakiness* suite to deliberately trigger failures for exploring analysis features.
* Format code once with `python -m ruff format .` (CI treats format drift as advisory).

## Project layout

```
app/
  main.py                 app factory, security headers, lifespan (DB init, admin bootstrap)
  config.py               settings from .env
  api/schemas.py          Pydantic request models + serializers
  api/deps.py             auth/CSRF dependency, URL policy
  api/routes/             auth, workbench, runs, reports, analytics, perf, ai, environments, demo, meta
  core/security.py        hashing, sessions, encryption, redaction, SSRF policy, rate limiter
  core/test_runner.py     allow-listed suites, bounded execution, JUnit parsing
  db/                     SQLAlchemy engine, models, repository
  services/               analytics, reports, AI, HTTP client, load generator
  static/                 index.html, css/styles.css, js/app.js
tests/                    conftest.py, api/ (automation), dashboard/ (backend + e2e)
test_data/schemas/        JSON Schemas used by the API tests
.github/workflows/ci.yml  Ruff + PyTest on Python 3.11 and 3.12
```

## Configuration

All settings live in `.env` (documented in `.env.example`). Highlights:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/api_dashboard.db` | Created automatically |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / blank | Blank generates a password on first start |
| `SECRET_KEY` | blank | Blank generates and stores `data/.secret_key` |
| `ENABLE_DEMO_API` | `true` | Mounts `/demo/*`, the default test target |
| `ALLOW_LOCAL_TARGETS` | `true` | Loopback targets |
| `ALLOW_PRIVATE_NETWORK_TARGETS` | `false` | RFC1918 ranges |
| `ALLOW_EXTERNAL_TARGETS` | `false` | Public hosts (see Security) |
| `EXTERNAL_TARGET_ALLOWLIST` | blank | Comma-separated hostnames permitted when external is on |
| `MAX_CONCURRENT_RUNS`, `MAX_ACTIVE_RUNS`, `TEST_TIMEOUT_SECONDS`, `TEST_RETRY_COUNT` | 1, 5, 180, 2 | Execution bounds |
| `PERF_MAX_REQUESTS`, `PERF_MAX_CONCURRENCY`, `PERF_MAX_DURATION_SECONDS` | 500, 20, 60 | Load-test safety limits |

## Optional AI integration

With `AI_PROVIDER=none` (default) the rules-based analysis is used and nothing leaves your machine. To use an LLM, edit `.env`:

```
AI_PROVIDER=openai_compatible
AI_API_KEY=<your key>
AI_BASE_URL=https://api.openai.com/v1      # or e.g. http://localhost:11434/v1 for Ollama
AI_MODEL=gpt-4o-mini
```
Restart the server. Any OpenAI-compatible `/chat/completions` endpoint works. Behaviour:

* Failure messages and test names are sanitised first (tokens, passwords, JWTs, e-mails and long opaque strings removed) and truncated.
* The rules-based **facts** are always shown; LLM output is shown as **hypotheses** with a confidence label.
* If the provider errors or times out, the rules-based result is returned with a notice.
* Generated pytest drafts are built locally from validated case data and displayed as text. The application never executes AI output.
* Every analysis is stored in the `ai_analyses` table and visible under **AI assistant > History**.

## Security model and limitations

* **Authentication:** one admin user, scrypt-hashed password, HMAC-signed `HttpOnly; SameSite=Strict` session cookie, CSRF header required on state-changing calls, login attempts rate-limited (in memory, per IP).
* **No arbitrary execution:** the UI only chooses a suite key from a fixed list; the pytest command line is built from constants and the test process receives a minimal environment (no admin password, secret key or AI key).
* **SSRF policy:** URLs must be http/https without embedded credentials. Hosts are resolved and every address is classified; link-local/metadata, multicast and reserved addresses are always blocked; loopback and private ranges follow the flags above; public hosts need `ALLOW_EXTERNAL_TARGETS=true`, the hostname in the allowlist (if set) **and** the per-request "I am authorised" confirmation. Redirects are followed manually (max 3) and re-validated at every hop.
* **Secrets:** saved credentials and sensitive environment-header values are Fernet-encrypted with a key derived from `SECRET_KEY` and never returned by the API. Existing plaintext environment headers are upgraded at startup. Logs, reports, stored output and AI input are redacted.
* **Cookies:** production defaults to secure session cookies; set `COOKIE_SECURE=false` only for local HTTP development.
* **Known limitations:** DNS rebinding between the check and the connection is not fully eliminated (the connection re-resolves); the session is a single shared admin account; rate limiting and run queue state are per process (run one worker); `/demo/*` is unauthenticated by design, so keep the default `127.0.0.1` bind and set `ENABLE_DEMO_API=false` if you expose the server; HTTPS is not terminated by the app (use a reverse proxy and `COOKIE_SECURE=true`). Do not expose this tool to the internet.
* Never share your `.env`, `data/` folder or `data/.secret_key`.

## Offline charts

Charts use Chart.js. The page tries `app/static/js/vendor/chart.umd.min.js` first, then the jsDelivr CDN. If neither is reachable, the rest of the dashboard still works and chart areas explain why they are empty. For fully offline use, download `chart.umd.min.js` (Chart.js 4.x) once and put it in that folder.

## Troubleshooting

* *Run ends in "error: Target ... is not reachable"*: the suite targets `TEST_TARGET_URL` or the URL you opened in the browser; make sure the server is reachable at that address and `ENABLE_DEMO_API=true`.
* *"Too many failed attempts"*: wait for `LOGIN_WINDOW_SECONDS` or restart the server.
* *Forgot the generated password*: stop the server, delete `data/api_dashboard.db`, set `ADMIN_PASSWORD` in `.env`, start again (this clears saved data).
