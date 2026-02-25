"""MoltBook identity verification service.

Verifies AI agent identity tokens against the MoltBook API.
See: https://moltbook.com/developers
"""

import logging
from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT = 10  # seconds


@dataclass
class MoltBookProfile:
    """Verified MoltBook agent profile."""

    moltbook_id: str
    username: str
    display_name: str
    karma: int
    verified: bool
    profile_url: str | None = None


async def verify_identity_token(token: str) -> MoltBookProfile:
    """Verify a MoltBook identity token and return the agent's profile.

    Raises HTTPException on invalid/expired tokens or API errors.
    """
    if not settings.moltbook_api_key:
        raise HTTPException(
            status_code=503,
            detail="MoltBook identity verification is not configured on this server",
        )

    async with httpx.AsyncClient(timeout=VERIFY_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{settings.moltbook_api_url}/identity/verify",
                headers={
                    "Authorization": f"Bearer {settings.moltbook_api_key}",
                    "Content-Type": "application/json",
                },
                json={"identity_token": token},
            )
        except httpx.TimeoutException:
            logger.error("MoltBook API timed out during identity verification")
            raise HTTPException(
                status_code=502,
                detail="MoltBook identity verification timed out",
            )
        except httpx.RequestError as e:
            logger.error("MoltBook API request failed: %s", e)
            raise HTTPException(
                status_code=502,
                detail="Failed to reach MoltBook identity service",
            )

    if resp.status_code == 401:
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired MoltBook identity token",
        )

    if resp.status_code != 200:
        logger.error(
            "MoltBook verify returned %d: %s", resp.status_code, resp.text[:500]
        )
        raise HTTPException(
            status_code=502,
            detail=f"MoltBook identity verification failed (status {resp.status_code})",
        )

    data = resp.json()
    agent_data = data.get("agent", data)

    return MoltBookProfile(
        moltbook_id=str(agent_data.get("id", agent_data.get("agent_id", ""))),
        username=agent_data.get("username", ""),
        display_name=agent_data.get("display_name", agent_data.get("name", "")),
        karma=int(agent_data.get("karma", 0)),
        verified=bool(agent_data.get("verified", False)),
        profile_url=agent_data.get("profile_url"),
    )
