import socket

import pytest

from app.core import security as sec


def fake_resolver(*ips):
    def resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return resolve


def test_password_hash_roundtrip_and_salting():
    h1, h2 = sec.hash_password("correct horse"), sec.hash_password("correct horse")
    assert h1 != h2
    assert sec.verify_password("correct horse", h1)
    assert not sec.verify_password("wrong", h1)
    assert not sec.verify_password("x", "not-a-valid-hash")


def test_session_token_roundtrip_expiry_and_tamper():
    token = sec.make_session_token("alice", "k", 60, now=1000)
    assert sec.verify_session_token(token, "k", now=1030) == "alice"
    assert sec.verify_session_token(token, "k", now=1061) is None
    assert sec.verify_session_token(token, "other-key", now=1030) is None
    payload, sig = token.split(".")
    assert sec.verify_session_token(payload + "x." + sig, "k", now=1030) is None
    assert sec.verify_session_token(None, "k") is None
    assert sec.verify_session_token("garbage", "k") is None


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("127.0.0.1", "loopback"),
        ("::1", "loopback"),
        ("10.0.0.5", "private"),
        ("192.168.1.1", "private"),
        ("172.16.0.1", "private"),
        ("169.254.169.254", "blocked"),
        ("0.0.0.0", "blocked"),
        ("224.0.0.1", "blocked"),
        ("8.8.8.8", "public"),
        ("::ffff:127.0.0.1", "loopback"),
        ("100.64.0.1", "private"),
    ],
)
def test_classify_ip(ip, expected):
    assert sec.classify_ip(ip) == expected


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://",
        "http://user:pw@127.0.0.1/",
        "",
        "http://127.0.0.1:99999/",
    ],
)
def test_bad_urls_blocked(url):
    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url(url, sec.UrlPolicy(), resolver=fake_resolver("127.0.0.1"))


def test_loopback_allowed_by_default_but_can_be_disabled():
    assert sec.evaluate_url("http://localhost:8000/x", sec.UrlPolicy(), resolver=fake_resolver("127.0.0.1"))
    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url("http://localhost/", sec.UrlPolicy(allow_local=False), resolver=fake_resolver("127.0.0.1"))


def test_metadata_and_private_addresses_blocked():
    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url(
            "http://169.254.169.254/latest/meta-data", sec.UrlPolicy(allow_private=True), resolver=fake_resolver("169.254.169.254")
        )
    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url("http://intranet.local/", sec.UrlPolicy(), resolver=fake_resolver("10.1.2.3"))
    assert sec.evaluate_url("http://intranet.local/", sec.UrlPolicy(allow_private=True), resolver=fake_resolver("10.1.2.3"))


def test_dns_rebinding_style_mixed_answers_use_strictest_rule():
    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url("http://evil.example/", sec.UrlPolicy(), resolver=fake_resolver("8.8.8.8", "127.0.0.1"))


def test_external_requires_flag_authorization_and_allowlist():
    public = fake_resolver("93.184.216.34")
    url = "https://api.example.com/v1"
    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url(url, sec.UrlPolicy(), True, public)  # globally disabled
    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url(url, sec.UrlPolicy(allow_external=True), False, public)  # not authorised
    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url(url, sec.UrlPolicy(allow_external=True, external_allowlist=("other.example",)), True, public)
    assert sec.evaluate_url(url, sec.UrlPolicy(allow_external=True, external_allowlist=("api.example.com",)), True, public)


def test_unresolvable_host_blocked():
    def boom(*a, **k):
        raise socket.gaierror("nope")

    with pytest.raises(sec.UrlBlocked):
        sec.evaluate_url("http://nonexistent.invalid/", sec.UrlPolicy(), resolver=boom)


@pytest.mark.parametrize(
    "text,secret",
    [
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("authorization=Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ('{"password": "hunter2", "ok": 1}', "hunter2"),
        ("GET /x?api_key=SECRET123&a=1", "SECRET123"),
        ("token=abcd1234", "abcd1234"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.sig123", "eyJzdWIiOiIxMjM0In0"),
    ],
)
def test_redact_text_removes_secrets(text, secret):
    out = sec.redact_text(text)
    assert secret not in out
    assert "REDACTED" in out


def test_redact_text_keeps_harmless_text_and_truncates():
    assert sec.redact_text("assert 500 == 200") == "assert 500 == 200"
    assert sec.redact_text("x" * 50, max_len=10).startswith("x" * 10)


def test_redact_obj_and_headers():
    obj = sec.redact_obj({"user": "a", "Password": "p", "nested": {"api_key": "k", "list": [{"token": "t"}, "Bearer zzz"]}})
    assert obj["Password"] == sec.REDACTED
    assert obj["nested"]["api_key"] == sec.REDACTED
    assert obj["nested"]["list"][0]["token"] == sec.REDACTED
    assert "zzz" not in str(obj)
    assert sec.redact_headers({"Authorization": "x", "Accept": "json", "Set-Cookie": "s"}) == {
        "Authorization": sec.REDACTED,
        "Accept": "json",
        "Set-Cookie": sec.REDACTED,
    }


def test_login_rate_limiter():
    rl = sec.LoginRateLimiter(3, 60)
    for _ in range(3):
        assert not rl.blocked("ip", now=100)
        rl.record_failure("ip", now=100)
    assert rl.blocked("ip", now=101)
    assert not rl.blocked("ip", now=200)  # window passed
    rl.record_failure("other", now=100)
    rl.reset("other")
    assert not rl.blocked("other", now=100)


def test_secret_encryption_roundtrip():
    pytest.importorskip("cryptography")
    enc = sec.encrypt_secret("s3cret", "key")
    assert "s3cret" not in enc
    assert sec.decrypt_secret(enc, "key") == "s3cret"
    assert sec.decrypt_secret(enc, "different-key") is None
