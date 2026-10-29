"""Security helpers: password hashing, signed sessions, secret encryption,
redaction, SSRF-safe URL evaluation and a small login rate limiter."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

REDACTED = "***REDACTED***"

# --------------------------------------------------------------------------
# Passwords (scrypt from the standard library)
# --------------------------------------------------------------------------
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------
# Secret key + signed session tokens
# --------------------------------------------------------------------------
def load_or_create_secret_key(configured: str, data_dir: Path) -> str:
    """Use SECRET_KEY if configured, otherwise create one persisted in data/."""
    if configured:
        return configured
    data_dir.mkdir(parents=True, exist_ok=True)
    key_file = data_dir / ".secret_key"
    if key_file.exists():
        value = key_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    key_file.write_text(value, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError:  # pragma: no cover - not supported on some Windows setups
        pass
    return value


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session_token(username: str, secret_key: str, ttl_seconds: int, now: float | None = None) -> str:
    payload = _b64(json.dumps({"u": username, "exp": int((now or time.time()) + ttl_seconds)}).encode())
    sig = _b64(hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_session_token(token: str | None, secret_key: str, now: float | None = None) -> str | None:
    if not token or "." not in token:
        return None
    payload, _, sig = token.partition(".")
    expected = _b64(hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
        current_time = time.time() if now is None else now
        if int(data["exp"]) <= current_time:
            return None
        return str(data["u"])
    except (ValueError, KeyError, TypeError, OverflowError):
        return None


# --------------------------------------------------------------------------
# Encryption of stored credentials (Fernet from `cryptography`)
# --------------------------------------------------------------------------
def _fernet(secret_key: str):
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(b"api-dashboard-credentials:" + secret_key.encode()).digest())
    return Fernet(key)


def encrypt_secret(plain: str, secret_key: str) -> str:
    return _fernet(secret_key).encrypt(plain.encode("utf-8")).decode()


def decrypt_secret(token: str, secret_key: str) -> str | None:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet(secret_key).decrypt(token.encode()).decode("utf-8")
    except InvalidToken:
        return None


_ENCRYPTED_HEADER_PREFIX = "enc:"


def encrypt_headers(headers: dict[str, Any], secret_key: str) -> dict[str, Any]:
    """Encrypt values of headers that commonly contain credentials."""
    encrypted: dict[str, Any] = {}
    for name, value in headers.items():
        if is_sensitive_key(str(name)) and value is not None:
            encrypted[str(name)] = _ENCRYPTED_HEADER_PREFIX + encrypt_secret(str(value), secret_key)
        else:
            encrypted[str(name)] = value
    return encrypted


def decrypt_headers(headers: dict[str, Any], secret_key: str) -> dict[str, Any]:
    """Decrypt headers, while retaining compatibility with older plaintext rows."""
    decrypted: dict[str, Any] = {}
    for name, value in headers.items():
        if isinstance(value, str) and value.startswith(_ENCRYPTED_HEADER_PREFIX):
            plain = decrypt_secret(value[len(_ENCRYPTED_HEADER_PREFIX) :], secret_key)
            if plain is not None:
                decrypted[str(name)] = plain
                continue
        decrypted[str(name)] = value
    return decrypted


def redact_headers_for_display(headers: dict[str, Any]) -> dict[str, Any]:
    """Return environment headers without exposing credential values."""
    return {name: (REDACTED if is_sensitive_key(str(name)) else value) for name, value in headers.items()}


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------
_SENSITIVE_KEY_HINTS = ("password", "passwd", "secret", "token", "authorization", "api_key", "apikey", "api-key", "cookie")

_RE_AUTH = re.compile(r"(?i)\b(authorization|proxy-authorization)\b([\"']?\s*[:=]\s*[\"']?)(?:(?:bearer|basic)\s+)?[^\s,;\"']+")
_RE_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*")
_RE_KV = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?token|client[_-]?secret)\b([\"']?\s*[:=]\s*[\"']?)[^\s,;&\"'}]+"
)
_RE_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")


def redact_text(text: Any, max_len: int | None = None) -> str:
    value = "" if text is None else str(text)
    value = _RE_AUTH.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", value)
    value = _RE_BEARER.sub(f"Bearer {REDACTED}", value)
    value = _RE_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", value)
    value = _RE_JWT.sub("***REDACTED_JWT***", value)
    if max_len is not None and len(value) > max_len:
        value = value[:max_len] + "…[truncated]"
    return value


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SENSITIVE_KEY_HINTS)


def redact_obj(obj: Any) -> Any:
    """Recursively redact sensitive keys and secret-looking strings."""
    if isinstance(obj, dict):
        return {k: (REDACTED if is_sensitive_key(str(k)) else redact_obj(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def redact_headers(headers: dict[str, Any]) -> dict[str, Any]:
    return {k: (REDACTED if is_sensitive_key(str(k)) else v) for k, v in headers.items()}


# --------------------------------------------------------------------------
# SSRF protection / outbound URL policy
# --------------------------------------------------------------------------
class UrlBlocked(ValueError):
    """Raised when an outbound URL violates the configured policy."""


@dataclass(frozen=True)
class UrlPolicy:
    allow_local: bool = True
    allow_private: bool = False
    allow_external: bool = False
    external_allowlist: tuple[str, ...] = ()


def classify_ip(ip: str) -> str:
    """Return loopback | private | public | blocked."""
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        return "blocked"  # includes cloud metadata 169.254.169.254
    if not addr.is_global:
        return "private"
    return "public"


Resolver = Callable[..., list]


def evaluate_url(
    url: str,
    policy: UrlPolicy,
    authorized_external: bool = False,
    resolver: Resolver | None = None,
) -> str:
    """Validate ``url`` against the policy. Returns the url or raises UrlBlocked."""
    if not isinstance(url, str) or not url.strip():
        raise UrlBlocked("URL is required.")
    url = url.strip()
    if len(url) > 2048:
        raise UrlBlocked("URL is too long (max 2048 characters).")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UrlBlocked("Only http:// and https:// URLs are allowed.")
    if not parts.hostname:
        raise UrlBlocked("URL has no host.")
    if parts.username or parts.password:
        raise UrlBlocked("Credentials inside the URL are not allowed; use the Auth settings.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UrlBlocked("URL has an invalid port.") from exc

    host = parts.hostname.lower()
    resolve = resolver or socket.getaddrinfo
    try:
        infos = resolve(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise UrlBlocked(f"Host '{host}' could not be resolved.") from exc
    ips = {info[4][0] for info in infos}
    if not ips:
        raise UrlBlocked(f"Host '{host}' did not resolve to any address.")

    scopes = {classify_ip(ip) for ip in ips}
    if "blocked" in scopes:
        raise UrlBlocked("Target resolves to a blocked address (link-local, multicast or reserved).")
    if "loopback" in scopes and not policy.allow_local:
        raise UrlBlocked("Local (loopback) targets are disabled (ALLOW_LOCAL_TARGETS=false).")
    if "private" in scopes and not policy.allow_private:
        raise UrlBlocked("Private-network targets are disabled (ALLOW_PRIVATE_NETWORK_TARGETS=false).")
    if "public" in scopes:
        if not policy.allow_external:
            raise UrlBlocked("External targets are disabled (set ALLOW_EXTERNAL_TARGETS=true to enable).")
        if not authorized_external:
            raise UrlBlocked("External target requires explicit authorization: confirm you are allowed to test it.")
        if policy.external_allowlist and host not in policy.external_allowlist:
            raise UrlBlocked(f"Host '{host}' is not in EXTERNAL_TARGET_ALLOWLIST.")
    return url


# --------------------------------------------------------------------------
# Login rate limiting (in-memory, per key)
# --------------------------------------------------------------------------
class LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._attempts.get(key, []) if now - t < self.window]
        self._attempts[key] = recent
        return recent

    def blocked(self, key: str, now: float | None = None) -> bool:
        with self._lock:
            return len(self._prune(key, now or time.time())) >= self.max_attempts

    def record_failure(self, key: str, now: float | None = None) -> None:
        with self._lock:
            now = time.time() if now is None else now
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
