# Build status

Generated in a sandbox **without network access**. FastAPI, SQLAlchemy, Pydantic, uvicorn, PyTest, pytest-html, httpx and Ruff were **not installable there**, so the full application was never started and the PyTest suites were never run by PyTest. This file lists exactly what was and was not verified. Treat the "not verified" items as the first thing to check on your machine.

## Verified in the sandbox

| Check | How | Result |
|---|---|---|
| All 46 Python files compile | `py_compile` | pass |
| Cross-references: every `from app.x import y` target exists; every `repo.`/`ai.`/`an.`/`rs.` attribute used exists; no unused imports | custom AST script | pass |
| Frontend JavaScript syntax (`app.js`, 744 lines) | `node --check` | pass |
| Security unit tests: password hashing, session tokens, credential encryption, redaction, SSRF classification and policy (loopback/private/metadata/external/allowlist/DNS-mix), login rate limiter | 35 test cases run with a stdlib stand-in for pytest | 35 passed |
| Service unit tests: percentiles and insufficient-data rules, summaries, daily series, run comparison, flaky detection, JUnit parsing, suite allow-list, minimal runner environment, HTML/JSON/CSV reports (escaping, redaction, CSV formula injection), AI rules (explain, generate, missing coverage, flaky), LLM output sanitising | 27 test cases | 27 passed |
| HTTP client and load generator against a local stdlib server: GET/POST, auth modes, redirects (incl. redirect to metadata address refused), timeout and connection errors, body truncation, header validation, load metrics, percentile gating, deadline stop | 12 test cases | 12 passed |

Total executed: **74 test cases, 74 passed**. The stand-in runner is not part of the project; with PyTest installed these same files run under PyTest normally.

Running these tests found and fixed three real defects before delivery: a flaky-test rule that mislabelled a single pass-to-fail regression, a requirement parser that missed "must be a string at most N characters", and a fragile `-m` option override that was replaced by an explicit `RUN_DEMO_FAILURES` gate.

## Written but NOT executed (needs a first run)

* **Application startup** and every FastAPI route, the SQLAlchemy models/repository, database auto-creation, admin bootstrap, security-header middleware.
* **`tests/api/`** (45 test functions, many parametrized): the demo API plus the Requests/PyTest automation suites.
* **`tests/dashboard/test_dashboard_api.py`** (27 end-to-end tests): auth/CSRF, environments, workbench, saved-request encryption at rest, running suites through the dashboard, reports, analytics, flaky detection through repeated runs, AI endpoints, performance limits.
* **Browser UI**: the HTML/CSS/JS has never been rendered. Expect to possibly fix small visual or interaction issues.
* **Ruff**: `ruff check` (rules E4, E7, E9, F) was not run; `ruff format --check` will probably report formatting drift. Run `python -m ruff format .` once. CI treats the format step as advisory.
* **pytest-html reports**, **GitHub Actions workflow**, **Chart.js charts** (needs the CDN or a local copy).
* **LLM integration** with a real provider (only the rules-based path and output sanitising were tested).

## Suggested first run

```
pip install -r requirements.txt
python -m pytest tests/api            # automation framework against its own local demo server
python -m pytest                      # everything, including dashboard end-to-end tests
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
If something fails, the traceback will point at the exact file; the most likely candidates are Pydantic/FastAPI version differences and SQLAlchemy type-annotation resolution.

## Known limitations (by design)

Single admin account; in-memory login rate limiter and run queue (single process only); DNS rebinding not fully eliminated; `/demo/*` is unauthenticated (keep the localhost bind or disable it); external targets, private networks and HTTPS termination are disabled or out of scope by default. See README > Security model.
