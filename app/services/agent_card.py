"""A2A Agent Card fetch and validation."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Required top-level fields in an A2A Agent Card
_REQUIRED_FIELDS = {"name", "url", "version", "skills"}


async def fetch_agent_card(endpoint_url: str) -> dict[str, Any]:
    """Fetch and validate an A2A Agent Card from {endpoint_url}/.well-known/agent.json."""
    card_url = endpoint_url.rstrip("/") + "/.well-known/agent.json"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(card_url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise AgentCardError(f"Agent Card fetch failed: HTTP {e.response.status_code} from {card_url}")
        except httpx.RequestError as e:
            raise AgentCardError(f"Agent Card fetch failed: {e}")

    try:
        card = resp.json()
    except Exception:
        raise AgentCardError("Agent Card response is not valid JSON")

    validate_agent_card(card)
    return card


def validate_agent_card(card: dict[str, Any]) -> None:
    """Validate an A2A Agent Card has required structure."""
    if not isinstance(card, dict):
        raise AgentCardError("Agent Card must be a JSON object")

    missing = _REQUIRED_FIELDS - set(card.keys())
    if missing:
        raise AgentCardError(f"Agent Card missing required fields: {', '.join(sorted(missing))}")

    # Validate skills array
    skills = card.get("skills", [])
    if not isinstance(skills, list):
        raise AgentCardError("Agent Card 'skills' must be an array")

    for i, skill in enumerate(skills):
        if not isinstance(skill, dict):
            raise AgentCardError(f"Agent Card skills[{i}] must be an object")
        if "id" not in skill:
            raise AgentCardError(f"Agent Card skills[{i}] missing required 'id' field")

    # Validate url matches
    if "url" in card and not isinstance(card["url"], str):
        raise AgentCardError("Agent Card 'url' must be a string")


def extract_capabilities_from_card(card: dict[str, Any]) -> list[str]:
    """Extract capability tags from an A2A Agent Card's skills."""
    tags = set()
    for skill in card.get("skills", []):
        for tag in skill.get("tags", []):
            if isinstance(tag, str):
                tags.add(tag)
    return sorted(tags)


def get_skill_ids_from_card(card: dict[str, Any]) -> set[str]:
    """Get all skill IDs from an A2A Agent Card."""
    return {skill["id"] for skill in card.get("skills", []) if "id" in skill}


class AgentCardError(Exception):
    """Raised when Agent Card fetch or validation fails."""
    pass
