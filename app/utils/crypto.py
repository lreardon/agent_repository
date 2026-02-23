"""Ed25519 signature utilities using PyNaCl."""

import hashlib
import secrets
from datetime import UTC, datetime

from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def generate_keypair() -> tuple[str, str]:
    """Generate an Ed25519 keypair. Returns (private_key_hex, public_key_hex)."""
    signing_key = SigningKey.generate()
    private_hex = signing_key.encode(encoder=HexEncoder).decode()
    public_hex = signing_key.verify_key.encode(encoder=HexEncoder).decode()
    return private_hex, public_hex


def build_signature_message(
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> bytes:
    """Build the message to sign: timestamp\\nmethod\\npath\\nsha256(body)."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}\n{method}\n{path}\n{body_hash}"
    return message.encode()


def sign_request(
    private_key_hex: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> str:
    """Sign a request and return the hex-encoded signature."""
    signing_key = SigningKey(private_key_hex.encode(), encoder=HexEncoder)
    message = build_signature_message(timestamp, method, path, body)
    signed = signing_key.sign(message, encoder=HexEncoder)
    return signed.signature.decode()


def verify_signature(
    public_key_hex: str,
    signature_hex: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> bool:
    """Verify an Ed25519 signature. Returns True if valid, False otherwise."""
    try:
        verify_key = VerifyKey(public_key_hex.encode(), encoder=HexEncoder)
        message = build_signature_message(timestamp, method, path, body)
        verify_key.verify(message, HexEncoder.decode(signature_hex.encode()))
        return True
    except (BadSignatureError, Exception):
        return False


def generate_nonce() -> str:
    """Generate a cryptographically secure nonce."""
    return secrets.token_hex(16)


def is_timestamp_valid(timestamp: str, max_age_seconds: int = 30) -> bool:
    """Check if a timestamp is within the allowed window."""
    try:
        ts = datetime.fromisoformat(timestamp)
        if ts.tzinfo is None:
            return False
        now = datetime.now(UTC)
        delta = abs((now - ts).total_seconds())
        return delta <= max_age_seconds
    except (ValueError, TypeError):
        return False
