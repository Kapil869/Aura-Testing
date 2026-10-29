"""AI-assisted QA with a deterministic rules-based fallback.

Principles:
* Facts come only from recorded evidence; hypotheses are labelled as such.
* Anything sent to an external provider is sanitised first.
* AI output is data. Generated pytest drafts are text built locally from validated
  case dictionaries and are never executed by the application.
"""

from __future__ import annotations

import json
import pprint
import re
from typing import Any

import requests

from app.config import Settings
from app.core.security import redact_text
from app.services.analytics_service import detect_flaky

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


class LLMError(Exception):
    pass


# ------------------------------------------------------------------ plumbing
def sanitize_for_llm(text: Any, max_len: int = 3000) -> str:
    value = redact_text(text)
    value = _EMAIL.sub("[email]", value)
    value = _LONG_TOKEN.sub("[token]", value)
    return value[:max_len]


def llm_enabled(settings: Settings) -> bool:
    if settings.ai_provider != "openai_compatible":
        return False
    local = any(h in settings.ai_base_url for h in ("//localhost", "//127.0.0.1"))
    return bool(settings.ai_api_key) or local


def ai_status(settings: Settings) -> dict[str, Any]:
    on = llm_enabled(settings)
    return {
        "mode": "llm" if on else "rules",
        "provider": settings.ai_provider if on else "rules",
        "model": settings.ai_model if on else None,
    }


def _llm_json(settings: Settings, system: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if settings.ai_api_key:
        headers["Authorization"] = f"Bearer {settings.ai_api_key}"
    body = {
        "model": settings.ai_model,
        "temperature": 0.2,
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": system + " Respond with a single JSON object and nothing else."},
            {"role": "user", "content": json.dumps(payload)},
        ],
    }
    try:
        resp = requests.post(f"{settings.ai_base_url}/chat/completions", json=body, headers=headers, timeout=settings.ai_timeout_seconds)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        data = json.loads(content)
        if not isinstance(data, dict):
            raise LLMError("LLM did not return a JSON object.")
        return data
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"LLM call failed ({type(exc).__name__}).") from exc


def _strs(value: Any, limit: int = 8, size: int = 400) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v)[:size] for v in value[:limit] if isinstance(v, (str, int, float))]


def _hyps(value: Any) -> list[dict[str, str]]:
    out = []
    for item in (value if isinstance(value, list) else [])[:8]:
        if isinstance(item, dict) and item.get("text"):
            conf = str(item.get("confidence", "low")).lower()
            out.append(
                {"text": str(item["text"])[:400], "confidence": conf if conf in ("low", "medium", "high") else "low", "source": "llm"}
            )
        elif isinstance(item, str):
            out.append({"text": item[:400], "confidence": "low", "source": "llm"})
    return out


# ------------------------------------------------------------ failure explain
_ASSERT_STATUS = re.compile(r"assert\s+(\d{3})\s*==\s*(\d{3})")
_ASSERT_IN = re.compile(r"assert\s+(\d{3})\s+in\s+[\[(]([\d,\s]+)[\])]")


def explain_failure_rules(ev: dict[str, Any]) -> dict[str, Any]:
    text = f"{ev.get('message', '')}\n{ev.get('details', '')}"
    low = text.lower()
    facts = [f"Test '{ev.get('test')}' ended with outcome '{ev.get('outcome')}'.", f"It ran for {float(ev.get('duration_s') or 0):.3f}s."]
    hyps: list[dict[str, str]] = []
    steps: list[str] = []

    def hyp(t: str, c: str = "low") -> None:
        hyps.append({"text": t, "confidence": c, "source": "rules"})

    m = _ASSERT_STATUS.search(text) or _ASSERT_IN.search(text)
    if m:
        got = int(m.group(1))
        facts.append(f"The assertion compared status code {got} with the expected value(s) {m.group(2).strip()}.")
        if got >= 500:
            hyp("The server raised an unhandled error (5xx); the defect is likely in the API implementation, not the test.", "medium")
            steps += [
                "Check the API server logs around the run time.",
                "Re-run the same request in the Workbench to see the response body.",
            ]
        elif got == 404:
            hyp("The endpoint path or resource ID may be wrong, or test data was not created/was already deleted.", "medium")
            steps.append("Verify the URL and that earlier tests (or fixtures) created the resource.")
        elif got in (401, 403):
            hyp("Credentials are missing, expired or lack permission for this operation.", "medium")
            steps.append("Check the auth header/token the test sends against the environment configuration.")
        elif got in (400, 422):
            hyp("The request payload was rejected by validation; the test data may not match the current API contract.", "medium")
            steps.append("Compare the request body with the current schema/requirements.")
        elif got == 405:
            hyp("The HTTP method is not supported on this path.", "medium")
        else:
            hyp("The API returned a status the test did not anticipate; the contract may have changed.", "low")
    if "timeout" in low or "timed out" in low:
        facts.append("The failure message mentions a timeout.")
        hyp("The server responded slower than the client timeout (load, slow dependency, or a timeout that is too tight).", "medium")
        steps += [
            "Compare this test's duration with other runs on the Analytics page.",
            "Increase the timeout only after confirming the server is healthy.",
        ]
    if "connectionerror" in low or "connection refused" in low or "max retries exceeded" in low:
        facts.append("The failure message indicates the client could not connect to the target.")
        hyp("The target service was not running, listened on a different port, or a network/firewall rule blocked it.", "medium")
        steps.append("Confirm the target URL of the environment and that the service is up.")
    if "jsondecodeerror" in low or "expecting value" in low:
        facts.append("A JSON decoding error occurred while reading the response.")
        hyp("The response body was empty or not JSON (for example an HTML error page or a 204 response).", "medium")
    if "is a required property" in low or "is not of type" in low or "validationerror" in low or "schema" in low:
        facts.append("A JSON Schema validation message is present in the failure output.")
        hyp("The response shape no longer matches the schema in test_data/schemas (API change or schema out of date).", "medium")
        steps.append("Diff the actual response against the schema file named in the traceback.")
    if "keyerror" in low:
        hyp("A field the test reads is missing from the response.", "medium")
    if "response time" in low or "slower than" in low or "elapsed" in low:
        hyp("A performance assertion failed; the threshold may be too strict for this environment or the server is under load.", "low")
    hist = ev.get("history") or []
    if hist:
        bad = sum(1 for o in hist if o in ("failed", "error"))
        facts.append(f"Recorded history for this test: {len(hist)} runs, {bad} failing.")
        if 0 < bad < len(hist):
            hyp(
                "The test passes and fails across runs, which suggests flakiness (shared state, timing or ordering) rather than a deterministic defect.",
                "medium",
            )
        elif bad == len(hist) and len(hist) >= 3:
            hyp("The test fails consistently, which points to a real regression or a broken test rather than flakiness.", "medium")
    if not hyps:
        hyp("The recorded evidence does not match a known pattern; read the full traceback below to locate the failing assertion.", "low")
    if not steps:
        steps.append("Read the traceback, then reproduce the request in the Workbench.")
    return {"facts": facts, "hypotheses": hyps, "next_steps": steps, "summary": None}


def explain_failure(settings: Settings, evidence: dict[str, Any]) -> dict[str, Any]:
    result = explain_failure_rules(evidence)
    result.update(provider="rules", llm_used=False, notice="Facts are taken from recorded results; hypotheses are unverified guesses.")
    if llm_enabled(settings):
        payload = {
            "test": evidence.get("test"),
            "outcome": evidence.get("outcome"),
            "duration_s": evidence.get("duration_s"),
            "message": sanitize_for_llm(evidence.get("message")),
            "details": sanitize_for_llm(evidence.get("details")),
            "history": evidence.get("history", [])[-15:],
            "rule_facts": result["facts"],
        }
        try:
            data = _llm_json(
                settings,
                "You are a senior QA engineer. Use ONLY the evidence given. Return keys: summary (string), "
                "hypotheses (list of {text, confidence: low|medium|high}), next_steps (list of strings). "
                "Do not present guesses as facts.",
                payload,
            )
            result["summary"] = str(data.get("summary", ""))[:800] or None
            result["hypotheses"] += _hyps(data.get("hypotheses"))
            result["next_steps"] += _strs(data.get("next_steps"))
            result.update(provider=settings.ai_model, llm_used=True)
        except LLMError as exc:
            result["notice"] += f" LLM unavailable, showing rules-based analysis ({exc})."
    return result


# ------------------------------------------------------------ test generation
_METHOD_PATH = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[\w/\-{}:.]*)", re.I)
_PRE = r"(?:\s+(?:is|are)\s+required\s+and)?"  # "name is required and must be ..."
_ART = r"(?:an?\s+\w+\s+)?"  # optional "a string " before a limit
_TYPE = re.compile(rf"\b(\w+){_PRE}\s+(?:must|should|has to)\s+be\s+(?:a|an)?\s*(string|integer|int|number|email|boolean|bool)\b", re.I)
_PAREN_TYPE = re.compile(r"\b(\w+)\s*\(\s*(string|integer|int|number|email|boolean|bool)\b([^)]*)\)", re.I)
_BETWEEN = re.compile(rf"\b(\w+){_PRE}\s+(?:must|should)\s+be\s+{_ART}between\s+(\d+)\s+and\s+(\d+)", re.I)
_BETWEEN_BARE = re.compile(r"\b(\w+)\s+between\s+(\d+)\s+and\s+(\d+)", re.I)
_AT_MOST = re.compile(rf"\b(\w+){_PRE}\s+(?:must|should)\s+be\s+{_ART}(?:at most|no more than|max(?:imum)?(?: of)?)\s+(\d+)", re.I)
_AT_LEAST = re.compile(rf"\b(\w+){_PRE}\s+(?:must|should)\s+be\s+{_ART}(?:at least|no less than|min(?:imum)?(?: of)?)\s+(\d+)", re.I)
_REQ_IS = re.compile(r"\b(\w+)\s+(?:is|are)\s+required", re.I)
_REQ_LIST = re.compile(r"required\s+fields?\s*(?:are|is|:)\s*([\w\s,and]+)", re.I)
_STATUSES = re.compile(r"\b([2345]\d{2})\b")
_STOP = {"the", "and", "all", "field", "fields", "request", "body", "it", "this", "that", "which", "value"}


def _case(title, category, method, path, body=None, headers=None, expected=None, raw_body=None, note=""):
    return {
        "title": title,
        "category": category,
        "method": method,
        "path": path,
        "body": body,
        "raw_body": raw_body,
        "headers": headers or {},
        "expected_status": expected or [400, 422],
        "note": note,
    }


def generate_tests_rules(requirement: str) -> dict[str, Any]:
    text = requirement.strip()
    assumptions: list[str] = []
    mp = _METHOD_PATH.search(text)
    if mp:
        method, path = mp.group(1).upper(), mp.group(2)
    else:
        method, path = "POST", "/resource"
        assumptions.append("No 'METHOD /path' found in the requirement; defaulted to POST /resource.")
    fields: dict[str, dict[str, Any]] = {}

    def f(name: str) -> dict[str, Any]:
        return fields.setdefault(name.lower(), {})

    for name, kind in _TYPE.findall(text):
        if name.lower() not in _STOP:
            f(name)["type"] = {"int": "integer", "bool": "boolean"}.get(kind.lower(), kind.lower())
    # Also accept compact requirement syntax such as `name (string, at most 50 characters)`.
    for name, kind, constraints in _PAREN_TYPE.findall(text):
        if name.lower() in _STOP:
            continue
        spec = f(name)
        spec["type"] = {"int": "integer", "bool": "boolean"}.get(kind.lower(), kind.lower())
        max_match = re.search(r"\b(?:at most|no more than|max(?:imum)?(?: of)?)\s+(\d+)", constraints, re.I)
        min_match = re.search(r"\b(?:at least|no less than|min(?:imum)?(?: of)?)\s+(\d+)", constraints, re.I)
        between_match = re.search(r"\bbetween\s+(\d+)\s+and\s+(\d+)", constraints, re.I)
        if max_match:
            spec["max"] = int(max_match.group(1))
        if min_match:
            spec["min"] = int(min_match.group(1))
        if between_match:
            spec.update(min=int(between_match.group(1)), max=int(between_match.group(2)))
    for name, lo, hi in [*_BETWEEN.findall(text), *_BETWEEN_BARE.findall(text)]:
        f(name).update(min=int(lo), max=int(hi))
        fields[name.lower()].setdefault("type", "integer")
    for name, hi in _AT_MOST.findall(text):
        f(name)["max"] = int(hi)
    for name, lo in _AT_LEAST.findall(text):
        f(name)["min"] = int(lo)
    required = {n.lower() for n in _REQ_IS.findall(text) if n.lower() not in _STOP}
    # Natural-language shorthand: `requires name (...)` means name is required.
    required.update(
        n.lower()
        for n in re.findall(r"\brequires?\s+(?:the\s+)?([a-zA-Z_]\w*)", text, re.I)
        if n.lower() not in _STOP and n.lower() not in {"a", "an"}
    )
    for chunk in _REQ_LIST.findall(text):
        required |= {w.lower() for w in re.split(r"[,\s]+|and", chunk) if w and w.lower() not in _STOP and w.isidentifier()}
    for n in required:
        f(n)
    for name, spec in fields.items():
        spec["required"] = name in required
    if not fields and method in ("POST", "PUT", "PATCH"):
        assumptions.append("No fields detected; only generic payload cases were generated.")

    def valid_value(name: str, spec: dict[str, Any]) -> Any:
        t = spec.get("type", "string")
        if t == "email":
            return "user@example.com"
        if t in ("integer", "number"):
            return spec.get("min", 1)
        if t == "boolean":
            return True
        return "x" * max(1, min(spec.get("min", 1), 20))

    valid = {n: valid_value(n, s) for n, s in fields.items()}
    mentioned = [int(s) for s in _STATUSES.findall(text)]
    ok_status = next((s for s in mentioned if 200 <= s < 300), 201 if method == "POST" else 200)
    bad = [s for s in mentioned if s in (400, 422)] or [400, 422]
    sends_body = method in ("POST", "PUT", "PATCH")
    cases = [_case(f"valid request returns {ok_status}", "positive", method, path, valid if sends_body else None, expected=[ok_status])]

    if sends_body:
        cases.append(_case("empty JSON object is rejected", "negative", method, path, {}, expected=bad))
        cases.append(
            _case(
                "malformed JSON is rejected",
                "invalid_input",
                method,
                path,
                None,
                {"Content-Type": "application/json"},
                bad,
                raw_body="{not json",
            )
        )
        for name, spec in fields.items():
            if spec["required"]:
                body = {k: v for k, v in valid.items() if k != name}
                cases.append(_case(f"missing required field '{name}'", "negative", method, path, body, expected=bad))
                cases.append(
                    _case(f"null value for required field '{name}'", "negative", method, path, {**valid, name: None}, expected=bad)
                )
            t = spec.get("type", "string")
            wrong = 12345 if t in ("string", "email") else "not-a-number" if t in ("integer", "number") else "not-a-bool"
            cases.append(_case(f"wrong type for '{name}'", "invalid_input", method, path, {**valid, name: wrong}, expected=bad))
            if t == "email":
                cases.append(
                    _case(f"malformed email in '{name}'", "invalid_input", method, path, {**valid, name: "not-an-email"}, expected=bad)
                )
            if t in ("integer", "number"):
                if "min" in spec:
                    cases.append(
                        _case(
                            f"'{name}' at minimum ({spec['min']})",
                            "boundary",
                            method,
                            path,
                            {**valid, name: spec["min"]},
                            expected=[ok_status],
                        )
                    )
                    cases.append(
                        _case(
                            f"'{name}' below minimum ({spec['min'] - 1})",
                            "boundary",
                            method,
                            path,
                            {**valid, name: spec["min"] - 1},
                            expected=bad,
                        )
                    )
                if "max" in spec:
                    cases.append(
                        _case(
                            f"'{name}' at maximum ({spec['max']})",
                            "boundary",
                            method,
                            path,
                            {**valid, name: spec["max"]},
                            expected=[ok_status],
                        )
                    )
                    cases.append(
                        _case(
                            f"'{name}' above maximum ({spec['max'] + 1})",
                            "boundary",
                            method,
                            path,
                            {**valid, name: spec["max"] + 1},
                            expected=bad,
                        )
                    )
            elif t == "string":
                if "max" in spec:
                    cases.append(
                        _case(
                            f"'{name}' at max length ({spec['max']})",
                            "boundary",
                            method,
                            path,
                            {**valid, name: "a" * spec["max"]},
                            expected=[ok_status],
                        )
                    )
                    cases.append(
                        _case(
                            f"'{name}' over max length ({spec['max'] + 1})",
                            "boundary",
                            method,
                            path,
                            {**valid, name: "a" * (spec["max"] + 1)},
                            expected=bad,
                        )
                    )
                if spec.get("min", 0) >= 1:
                    cases.append(
                        _case(
                            f"'{name}' below min length", "boundary", method, path, {**valid, name: "a" * (spec["min"] - 1)}, expected=bad
                        )
                    )
                cases.append(
                    _case(
                        f"'{name}' empty string",
                        "boundary",
                        method,
                        path,
                        {**valid, name: ""},
                        expected=bad if spec.get("required") else [ok_status, *bad],
                    )
                )
    if re.search(r"authenticat|authoriz|bearer|api key|token|logged in", text, re.I):
        cases.append(
            _case("request without credentials is rejected", "auth", method, path, valid if sends_body else None, expected=[401, 403])
        )
        cases.append(
            _case(
                "request with invalid token is rejected",
                "auth",
                method,
                path,
                valid if sends_body else None,
                {"Authorization": "Bearer invalid-token"},
                [401, 403],
            )
        )
    other = "DELETE" if method != "DELETE" else "PUT"
    cases.append(
        _case(
            f"{other} on this path is not allowed",
            "negative",
            other,
            path,
            None,
            expected=[404, 405],
            note="Adjust if the API supports this method.",
        )
    )
    return {"cases": cases[:60], "assumptions": assumptions}


def render_pytest_draft(cases: list[dict[str, Any]]) -> str:
    """Build a draft pytest module as TEXT. Strings are embedded via repr(), never evaluated."""
    literal = pprint.pformat(cases, width=110, sort_dicts=False)
    return (
        '"""DRAFT generated by API Testing Dashboard. Review and adapt before using.\n'
        'Fixtures `base_url` and `http` come from tests/conftest.py."""\n'
        "import pytest\n\n"
        f"CASES = {literal}\n\n\n"
        '@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])\n'
        "def test_generated(case, base_url, http):\n"
        "    response = http.request(\n"
        '        case["method"], base_url + case["path"],\n'
        '        json=case["body"], data=case["raw_body"], headers=case["headers"] or None,\n'
        "    )\n"
        '    assert response.status_code in case["expected_status"], response.text\n'
    )


def _clean_cases(raw: Any) -> list[dict[str, Any]]:
    out = []
    for c in (raw if isinstance(raw, list) else [])[:60]:
        if not isinstance(c, dict):
            continue
        method = str(c.get("method", "GET")).upper()
        path = str(c.get("path", "/"))
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not path.startswith("/"):
            continue
        exp = [int(s) for s in c.get("expected_status", []) if isinstance(s, int) and 100 <= s <= 599] or [400]
        body = c.get("body") if isinstance(c.get("body"), (dict, list)) else None
        hdrs = {str(k)[:80]: str(v)[:200] for k, v in (c.get("headers") or {}).items()} if isinstance(c.get("headers"), dict) else {}
        out.append(
            _case(
                str(c.get("title", "generated case"))[:150],
                str(c.get("category", "negative"))[:30],
                method,
                path[:300],
                body,
                hdrs,
                exp,
                note=str(c.get("note", ""))[:300],
            )
        )
    return out


def generate_tests(settings: Settings, requirement: str) -> dict[str, Any]:
    result = generate_tests_rules(requirement)
    result.update(provider="rules", llm_used=False, notice="Draft only. Review before use; nothing is executed automatically.")
    if llm_enabled(settings):
        try:
            data = _llm_json(
                settings,
                "You design API test cases. From the requirement, produce positive, negative, boundary and invalid-input cases. "
                "Return {cases:[{title, category, method, path, body, headers, expected_status:[ints], note}], assumptions:[strings]}. "
                "Do not include code.",
                {"requirement": sanitize_for_llm(requirement, 4000)},
            )
            cases = _clean_cases(data.get("cases"))
            if cases:
                result.update(cases=cases, assumptions=_strs(data.get("assumptions")), provider=settings.ai_model, llm_used=True)
        except LLMError as exc:
            result["notice"] += f" LLM unavailable, showing rules-based draft ({exc})."
    result["pytest_draft"] = render_pytest_draft(result["cases"])
    return result


# ------------------------------------------------------------ missing coverage
_COVERAGE = {
    "negative": (
        ("invalid", "negative", "missing", "bad", "reject"),
        "Add negative tests that send invalid or missing fields and expect 400/422.",
        "test_create_user_missing_name_returns_422",
    ),
    "boundary": (
        ("boundary", "min", "max", "limit", "empty", "zero", "too_long", "overflow"),
        "Add boundary tests at, just below and just above each limit.",
        "test_age_120_ok_121_rejected",
    ),
    "authentication": (
        ("auth", "token", "unauthor", "forbidden", "401", "403"),
        "Test missing, invalid and expired credentials.",
        "test_protected_without_token_returns_401",
    ),
    "schema": (("schema",), "Validate response bodies against a JSON Schema, not just key presence.", "test_get_user_matches_schema"),
    "timeouts": (
        ("timeout", "slow", "latency"),
        "Verify the client handles slow responses and timeouts gracefully.",
        "test_slow_endpoint_raises_timeout",
    ),
    "headers": (
        ("header", "content_type", "content-type"),
        "Assert important headers (Content-Type, Location, caching).",
        "test_create_returns_location_header",
    ),
    "pagination": (
        ("pagination", "offset", "page", "limit"),
        "Test limit/offset edges: 0, max, beyond the last page.",
        "test_list_users_limit_zero_rejected",
    ),
    "idempotency": (
        ("idempotent", "twice", "duplicate", "repeat"),
        "Test repeating PUT/DELETE and duplicate POSTs.",
        "test_delete_twice_returns_404",
    ),
    "method handling": (
        ("405", "method_not_allowed", "wrong_method"),
        "Check unsupported methods return 405.",
        "test_patch_on_collection_returns_405",
    ),
    "not found": (("404", "not_found", "unknown", "nonexistent"), "Request IDs that do not exist.", "test_get_unknown_user_returns_404"),
    "special characters": (
        ("unicode", "injection", "special", "emoji"),
        "Try unicode, quotes, very long strings and injection-like input.",
        "test_name_with_unicode_and_quotes",
    ),
    "performance": (
        ("response_time", "elapsed", "perf", "under_"),
        "Assert a response-time budget on key endpoints.",
        "test_health_responds_under_500ms",
    ),
}


def suggest_missing_rules(test_names: list[str]) -> dict[str, Any]:
    joined = " ".join(test_names).lower()
    covered, missing = [], []
    for category, (keywords, advice, example) in _COVERAGE.items():
        if any(k in joined for k in keywords):
            covered.append(category)
        else:
            missing.append({"category": category, "suggestion": advice, "example_test_name": example})
    return {"tests_examined": len(test_names), "covered_categories": covered, "missing": missing}


def suggest_missing(settings: Settings, test_names: list[str]) -> dict[str, Any]:
    result = suggest_missing_rules(test_names)
    result.update(
        provider="rules", llm_used=False, notice="Heuristic keyword match on test names; a category may be covered under a different name."
    )
    if llm_enabled(settings) and test_names:
        try:
            data = _llm_json(
                settings,
                "You review API test suites. Given test names, list important missing negative and boundary cases. "
                "Return {extra_suggestions:[strings]}.",
                {"test_names": [sanitize_for_llm(n, 200) for n in test_names[:150]]},
            )
            result["extra_suggestions"] = _strs(data.get("extra_suggestions"), 10)
            result.update(provider=settings.ai_model, llm_used=True)
        except LLMError as exc:
            result["notice"] += f" LLM unavailable ({exc})."
    return result


# ------------------------------------------------------------ flaky analysis
def flaky_analysis(settings: Settings, history: dict[str, list[str]], last_messages: dict[str, str]) -> dict[str, Any]:
    flaky = detect_flaky(history)
    items = []
    for f in flaky:
        msg = (last_messages.get(f["nodeid"]) or "").lower()
        hyps = []
        if "timeout" in msg or "timed out" in msg:
            hyps.append("Timing sensitivity: the last failure mentions a timeout.")
        if re.search(r"\b5\d\d\b", msg):
            hyps.append("The server intermittently returns 5xx; look for load, resource or dependency issues.")
        hyps += [
            "Shared mutable state or test-order dependence (data left by other tests).",
            "External or time-dependent behaviour not controlled by the test.",
        ]
        items.append(
            {
                **f,
                "facts": [
                    f"Failed {f['failures']} of {f['runs']} recorded runs; outcome changed in {int(f['flip_rate'] * 100)}% of consecutive runs."
                ],
                "hypotheses": [{"text": h, "confidence": "low", "source": "rules"} for h in hyps],
            }
        )
    result = {
        "flaky_tests": items,
        "runs_considered": max((len(v) for v in history.values()), default=0),
        "provider": "rules",
        "llm_used": False,
        "notice": "A test needs at least 4 recorded runs with both passes and failures to be flagged.",
        "summary": None,
    }
    if not items:
        result["summary"] = "No flaky tests detected in the recorded history."
    elif llm_enabled(settings):
        try:
            data = _llm_json(
                settings,
                "You are a QA lead. Summarise likely flakiness themes from the data. Return {summary: string}.",
                {
                    "flaky": [{k: i[k] for k in ("nodeid", "runs", "failures", "flip_rate", "recent")} for i in items[:15]],
                    "last_messages": {k: sanitize_for_llm(v, 300) for k, v in list(last_messages.items())[:15]},
                },
            )
            result["summary"] = str(data.get("summary", ""))[:1000] or None
            result.update(provider=settings.ai_model, llm_used=True)
        except LLMError as exc:
            result["notice"] += f" LLM unavailable ({exc})."
    return result
