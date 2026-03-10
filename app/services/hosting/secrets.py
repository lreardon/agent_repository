"""Agent secret management — encrypt at rest, inject at deploy time."""

import logging
import os
import uuid

from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.hosting import AgentSecret

logger = logging.getLogger(__name__)

# Derive encryption key from platform signing key.
# In production this should be a dedicated KMS-backed key.
_ENCRYPTION_KEY: bytes | None = None


def _get_fernet() -> Fernet:
    global _ENCRYPTION_KEY
    if _ENCRYPTION_KEY is None:
        import hashlib
        # Derive a 32-byte key from the platform signing key
        raw = hashlib.sha256(settings.platform_signing_key.encode()).digest()
        import base64
        _ENCRYPTION_KEY = base64.urlsafe_b64encode(raw)
    return Fernet(_ENCRYPTION_KEY)


def encrypt_value(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode())


def decrypt_value(ciphertext: bytes) -> str:
    return _get_fernet().decrypt(ciphertext).decode()


async def set_secret(
    db: AsyncSession,
    agent_id: uuid.UUID,
    key: str,
    value: str,
) -> AgentSecret:
    """Create or update a secret for an agent."""
    encrypted = encrypt_value(value)

    # Upsert: delete existing then insert
    await db.execute(
        delete(AgentSecret).where(
            AgentSecret.agent_id == agent_id,
            AgentSecret.key == key,
        )
    )

    secret = AgentSecret(
        agent_id=agent_id,
        key=key,
        encrypted_value=encrypted,
    )
    db.add(secret)
    await db.flush()
    return secret


async def get_secret(
    db: AsyncSession,
    agent_id: uuid.UUID,
    key: str,
) -> str | None:
    """Retrieve and decrypt a secret."""
    result = await db.execute(
        select(AgentSecret).where(
            AgentSecret.agent_id == agent_id,
            AgentSecret.key == key,
        )
    )
    secret = result.scalar_one_or_none()
    if secret is None:
        return None
    return decrypt_value(secret.encrypted_value)


async def list_secrets(
    db: AsyncSession,
    agent_id: uuid.UUID,
) -> list[AgentSecret]:
    """List all secrets for an agent (keys + metadata only, no values)."""
    result = await db.execute(
        select(AgentSecret).where(AgentSecret.agent_id == agent_id)
    )
    return list(result.scalars().all())


async def delete_secret(
    db: AsyncSession,
    agent_id: uuid.UUID,
    key: str,
) -> bool:
    """Delete a secret. Returns True if it existed."""
    result = await db.execute(
        delete(AgentSecret).where(
            AgentSecret.agent_id == agent_id,
            AgentSecret.key == key,
        )
    )
    return result.rowcount > 0


async def get_all_decrypted(
    db: AsyncSession,
    agent_id: uuid.UUID,
) -> dict[str, str]:
    """Retrieve all secrets for an agent, decrypted. Used at deploy time."""
    secrets = await list_secrets(db, agent_id)
    return {s.key: decrypt_value(s.encrypted_value) for s in secrets}
