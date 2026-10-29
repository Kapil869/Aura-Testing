"""Shared helpers for API tests: JSON Schema loading and unique test data."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "test_data" / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def assert_matches_schema(data, name: str) -> None:
    """Validate ``data`` against test_data/schemas/<name>.json; raises ValidationError."""
    Draft7Validator(load_schema(name)).validate(data)


def unique_email(prefix: str = "qa") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@example.com"
