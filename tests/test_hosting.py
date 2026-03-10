"""Tests for agent hosting: manifest parsing, secrets, deploy service."""

import base64
import io
import json
import tarfile
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.hosting.manifest import (
    AgentManifest,
    SkillDef,
    extract_secret_refs,
    parse_manifest,
)
from app.services.hosting.secrets import decrypt_value, encrypt_value


# ─── Manifest Parsing ───────────────────────────────────────────────


class TestManifestParsing:
    def test_minimal_manifest(self):
        data = {
            "name": "test-agent",
            "runtime": "python:3.13",
            "skills": [
                {"id": "echo", "description": "Echo service"},
            ],
        }
        m = parse_manifest(data)
        assert m.name == "test-agent"
        assert m.runtime == "python:3.13"
        assert len(m.skills) == 1
        assert m.skills[0].id == "echo"
        assert m.cpu == "0.25"
        assert m.memory_mb == 512
        assert m.entrypoint == "handler.py"

    def test_full_manifest(self):
        data = {
            "name": "full-agent",
            "runtime": "python:3.12",
            "skills": [
                {
                    "id": "pdf-extract",
                    "description": "Extract data from PDFs",
                    "base_price": "1.50",
                },
                {
                    "id": "ocr",
                    "description": "Optical character recognition",
                },
            ],
            "requirements": ["poppler-utils", "tesseract-ocr"],
            "env": {
                "OPENAI_API_KEY": "${secrets.OPENAI_API_KEY}",
                "LOG_LEVEL": "INFO",
            },
            "cpu": "1",
            "memory_mb": 2048,
            "entrypoint": "main.py",
        }
        m = parse_manifest(data)
        assert m.name == "full-agent"
        assert m.runtime == "python:3.12"
        assert len(m.skills) == 2
        assert m.skills[0].base_price == "1.50"
        assert m.requirements == ["poppler-utils", "tesseract-ocr"]
        assert m.cpu == "1"
        assert m.memory_mb == 2048
        assert m.entrypoint == "main.py"

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            parse_manifest({"runtime": "python:3.13"})

    def test_invalid_runtime_raises(self):
        with pytest.raises(ValueError, match="runtime"):
            parse_manifest({"name": "test", "runtime": "go:1.21"})

    def test_duplicate_skill_id_raises(self):
        with pytest.raises(ValueError, match="Duplicate"):
            parse_manifest({
                "name": "test",
                "skills": [
                    {"id": "echo", "description": "One"},
                    {"id": "echo", "description": "Two"},
                ],
            })

    def test_invalid_skill_id_raises(self):
        with pytest.raises(ValueError, match="skills\\[0\\].id"):
            parse_manifest({
                "name": "test",
                "skills": [{"id": "invalid id!", "description": "Bad"}],
            })

    def test_missing_skill_description_raises(self):
        with pytest.raises(ValueError, match="description"):
            parse_manifest({
                "name": "test",
                "skills": [{"id": "echo"}],
            })

    def test_invalid_cpu_raises(self):
        with pytest.raises(ValueError, match="cpu"):
            parse_manifest({"name": "test", "cpu": "16"})

    def test_invalid_memory_raises(self):
        with pytest.raises(ValueError, match="memory_mb"):
            parse_manifest({"name": "test", "memory_mb": 99999})

    def test_env_key_must_be_upper_snake_case(self):
        with pytest.raises(ValueError, match="UPPER_SNAKE_CASE"):
            parse_manifest({
                "name": "test",
                "env": {"lowercase": "value"},
            })

    def test_too_many_skills_raises(self):
        skills = [{"id": f"skill-{i}", "description": f"Skill {i}"} for i in range(21)]
        with pytest.raises(ValueError, match="Maximum 20"):
            parse_manifest({"name": "test", "skills": skills})

    def test_not_a_dict_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_manifest("not a dict")


class TestSecretRefs:
    def test_extracts_secret_refs(self):
        env = {
            "API_KEY": "${secrets.API_KEY}",
            "LOG_LEVEL": "INFO",
            "OTHER_SECRET": "${secrets.OTHER_SECRET}",
        }
        refs = extract_secret_refs(env)
        assert sorted(refs) == ["API_KEY", "OTHER_SECRET"]

    def test_no_refs(self):
        env = {"LOG_LEVEL": "INFO", "PORT": "8080"}
        assert extract_secret_refs(env) == []


# ─── Secrets Encryption ─────────────────────────────────────────────


class TestSecretEncryption:
    def test_roundtrip(self):
        plaintext = "sk-secret-key-12345"
        encrypted = encrypt_value(plaintext)
        assert isinstance(encrypted, bytes)
        assert encrypted != plaintext.encode()
        decrypted = decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_different_values_produce_different_ciphertext(self):
        a = encrypt_value("value-a")
        b = encrypt_value("value-b")
        assert a != b

    def test_empty_string(self):
        encrypted = encrypt_value("")
        assert decrypt_value(encrypted) == ""

    def test_unicode_roundtrip(self):
        text = "key with unicode: \u2603 \U0001f600"
        assert decrypt_value(encrypt_value(text)) == text


# ─── Deploy Service Helpers ──────────────────────────────────────────


class TestDeployHelpers:
    def _make_archive(self, files: dict[str, str]) -> bytes:
        """Create a tar.gz archive from a dict of {filename: content}."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, content in files.items():
                data = content.encode()
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_extract_manifest_from_archive(self):
        from app.services.hosting.deploy import _extract_manifest

        manifest_content = json.dumps({
            "name": "test-agent",
            "runtime": "python:3.13",
            "skills": [{"id": "echo", "description": "Echo"}],
        })
        # YAML is a superset of JSON, so this works
        archive = self._make_archive({"arcoa.yaml": manifest_content})
        result = _extract_manifest(archive)
        assert result["name"] == "test-agent"

    def test_extract_manifest_missing_raises(self):
        from app.services.hosting.deploy import _extract_manifest

        archive = self._make_archive({"handler.py": "def handle(r): return r"})
        with pytest.raises(ValueError, match="arcoa.yaml"):
            _extract_manifest(archive)

    def test_path_traversal_rejected(self):
        from app.services.hosting.deploy import _extract_manifest

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="../../../etc/passwd")
            info.size = 4
            tar.addfile(info, io.BytesIO(b"evil"))
        with pytest.raises(ValueError, match="Unsafe path"):
            _extract_manifest(buf.getvalue())

    def test_generate_dockerfile(self):
        from app.services.hosting.deploy import _generate_dockerfile

        manifest = AgentManifest(
            name="test",
            runtime="python:3.13",
            skills=[SkillDef(id="echo", description="Echo")],
            requirements=["poppler-utils"],
            entrypoint="handler.py",
        )
        dockerfile = _generate_dockerfile(manifest)
        assert "FROM python:3.13-slim" in dockerfile
        assert "poppler-utils" in dockerfile
        assert "pip install --no-cache-dir arcoa" in dockerfile
        assert "USER agent" in dockerfile
        assert "_arcoa_entrypoint.py" in dockerfile

    def test_generate_dockerfile_node(self):
        from app.services.hosting.deploy import _generate_dockerfile

        manifest = AgentManifest(
            name="node-agent",
            runtime="node:20",
            skills=[SkillDef(id="echo", description="Echo")],
            entrypoint="handler.js",
        )
        dockerfile = _generate_dockerfile(manifest)
        assert "FROM node:20-slim" in dockerfile
        assert "npm ci" in dockerfile

    def test_generate_entrypoint(self):
        from app.services.hosting.deploy import _generate_entrypoint

        manifest = AgentManifest(
            name="test",
            runtime="python:3.13",
            skills=[SkillDef(id="echo", description="Echo")],
            entrypoint="handler.py",
        )
        entrypoint = _generate_entrypoint(manifest)
        assert "ARCOA_AGENT_ID" in entrypoint
        assert "/app/handler.py" in entrypoint
        assert "ArcoaAgent" in entrypoint
        assert "job.funded" in entrypoint
