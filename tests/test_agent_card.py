"""Tests for A2A Agent Card validation and integration."""

import pytest

from app.services.agent_card import (
    AgentCardError,
    extract_capabilities_from_card,
    get_skill_ids_from_card,
    validate_agent_card,
)


VALID_CARD = {
    "name": "Test Agent",
    "url": "https://agent.example.com",
    "version": "1.0.0",
    "capabilities": {"streaming": False, "pushNotifications": True},
    "skills": [
        {
            "id": "pdf_parse",
            "name": "PDF Data Extraction",
            "description": "Extracts structured JSON from PDF documents",
            "tags": ["pdf", "extraction", "structured-data"],
            "examples": ["Extract all tables from this PDF"],
        },
        {
            "id": "ocr",
            "name": "OCR Processing",
            "tags": ["ocr", "image"],
        },
    ],
    "defaultInputModes": ["application/json"],
    "defaultOutputModes": ["application/json"],
}


class TestValidateAgentCard:
    def test_valid_card(self) -> None:
        validate_agent_card(VALID_CARD)  # Should not raise

    def test_missing_required_fields(self) -> None:
        with pytest.raises(AgentCardError, match="missing required fields"):
            validate_agent_card({"name": "Test"})

    def test_not_a_dict(self) -> None:
        with pytest.raises(AgentCardError, match="must be a JSON object"):
            validate_agent_card("not a dict")  # type: ignore

    def test_skills_not_array(self) -> None:
        card = {**VALID_CARD, "skills": "not an array"}
        with pytest.raises(AgentCardError, match="must be an array"):
            validate_agent_card(card)

    def test_skill_missing_id(self) -> None:
        card = {**VALID_CARD, "skills": [{"name": "test"}]}
        with pytest.raises(AgentCardError, match="missing required 'id'"):
            validate_agent_card(card)


class TestExtractCapabilities:
    def test_extracts_tags(self) -> None:
        caps = extract_capabilities_from_card(VALID_CARD)
        assert "pdf" in caps
        assert "extraction" in caps
        assert "ocr" in caps
        assert "image" in caps

    def test_deduplicates(self) -> None:
        card = {
            **VALID_CARD,
            "skills": [
                {"id": "a", "tags": ["pdf", "ocr"]},
                {"id": "b", "tags": ["pdf", "text"]},
            ],
        }
        caps = extract_capabilities_from_card(card)
        assert caps.count("pdf") == 1

    def test_empty_skills(self) -> None:
        card = {**VALID_CARD, "skills": []}
        assert extract_capabilities_from_card(card) == []


class TestGetSkillIds:
    def test_gets_ids(self) -> None:
        ids = get_skill_ids_from_card(VALID_CARD)
        assert ids == {"pdf_parse", "ocr"}

    def test_empty(self) -> None:
        assert get_skill_ids_from_card({"skills": []}) == set()
