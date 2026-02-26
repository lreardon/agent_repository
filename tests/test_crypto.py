"""Unit tests for app/utils/crypto.py."""

from datetime import UTC, datetime, timedelta

from app.utils.crypto import (
    generate_keypair,
    generate_nonce,
    is_timestamp_valid,
    sign_request,
    verify_signature,
)


def test_generate_keypair_format() -> None:
    """C1: generate_keypair returns valid hex strings of correct length."""
    priv, pub = generate_keypair()
    assert len(priv) == 64  # 32 bytes hex
    assert len(pub) == 64
    bytes.fromhex(priv)
    bytes.fromhex(pub)


def test_sign_verify_round_trip() -> None:
    """C2: sign then verify succeeds."""
    priv, pub = generate_keypair()
    ts = datetime.now(UTC).isoformat()
    sig = sign_request(priv, ts, "POST", "/test", b'{"a":1}')
    assert verify_signature(pub, sig, ts, "POST", "/test", b'{"a":1}')


def test_verify_tampered_body() -> None:
    """C3: verify returns False for tampered body."""
    priv, pub = generate_keypair()
    ts = datetime.now(UTC).isoformat()
    sig = sign_request(priv, ts, "POST", "/test", b'{"a":1}')
    assert not verify_signature(pub, sig, ts, "POST", "/test", b'{"a":2}')


def test_timestamp_valid_naive_rejected() -> None:
    """C4: naive datetime (no timezone) returns False."""
    assert not is_timestamp_valid("2026-01-01T00:00:00", 30)


def test_timestamp_valid_garbage_rejected() -> None:
    """C5: invalid string returns False."""
    assert not is_timestamp_valid("not-a-date", 30)
    assert not is_timestamp_valid("", 30)


def test_timestamp_valid_in_window() -> None:
    ts = datetime.now(UTC).isoformat()
    assert is_timestamp_valid(ts, 30)


def test_timestamp_valid_expired() -> None:
    ts = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    assert not is_timestamp_valid(ts, 30)


def test_generate_nonce_format() -> None:
    """C6: nonce is 32-char hex."""
    n = generate_nonce()
    assert len(n) == 32
    bytes.fromhex(n)


def test_generate_nonce_unique() -> None:
    """Nonces should be unique."""
    nonces = {generate_nonce() for _ in range(100)}
    assert len(nonces) == 100


def test_generate_keypair_unique() -> None:
    """Each keypair call should produce different keys."""
    pairs = [generate_keypair() for _ in range(10)]
    privs = {p[0] for p in pairs}
    pubs = {p[1] for p in pairs}
    assert len(privs) == 10
    assert len(pubs) == 10


def test_verify_wrong_key_rejected() -> None:
    """Signature from one key doesn't verify against another."""
    priv1, _ = generate_keypair()
    _, pub2 = generate_keypair()
    ts = datetime.now(UTC).isoformat()
    sig = sign_request(priv1, ts, "GET", "/test", b"")
    assert not verify_signature(pub2, sig, ts, "GET", "/test", b"")


def test_verify_wrong_method_rejected() -> None:
    """Signature for GET doesn't verify for POST."""
    priv, pub = generate_keypair()
    ts = datetime.now(UTC).isoformat()
    sig = sign_request(priv, ts, "GET", "/test", b"")
    assert not verify_signature(pub, sig, ts, "POST", "/test", b"")


def test_verify_empty_body() -> None:
    """Sign/verify with empty body works."""
    priv, pub = generate_keypair()
    ts = datetime.now(UTC).isoformat()
    sig = sign_request(priv, ts, "DELETE", "/agents/123", b"")
    assert verify_signature(pub, sig, ts, "DELETE", "/agents/123", b"")
