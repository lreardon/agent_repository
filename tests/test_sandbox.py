"""Tests for the Docker-based sandbox executor.

These tests require Docker to be available. Tests that need Docker
are marked with @pytest.mark.docker and can be skipped in CI without Docker.
"""

import base64
import json

import pytest

from app.services.sandbox import (
    ALLOWED_RUNTIMES,
    MAX_SCRIPT_SIZE_BYTES,
    SandboxResult,
    run_script_in_sandbox,
    validate_script_criteria,
)


def _b64(script: str) -> str:
    """Helper to base64-encode a script string."""
    return base64.b64encode(script.encode()).decode()


# ---------------------------------------------------------------------------
# validate_script_criteria (unit tests — no Docker needed)
# ---------------------------------------------------------------------------

class TestValidateScriptCriteria:
    def test_valid_python_script(self) -> None:
        criteria = {
            "script": _b64("import json\nprint('ok')"),
            "runtime": "python:3.13",
            "timeout_seconds": 30,
            "memory_limit_mb": 128,
        }
        validate_script_criteria(criteria)  # Should not raise

    def test_missing_script(self) -> None:
        with pytest.raises(ValueError, match="must include 'script'"):
            validate_script_criteria({"runtime": "python:3.13"})

    def test_invalid_base64(self) -> None:
        with pytest.raises(ValueError, match="valid base64"):
            validate_script_criteria({"script": "not-valid-base64!!!"})

    def test_script_too_large(self) -> None:
        huge = base64.b64encode(b"x" * (MAX_SCRIPT_SIZE_BYTES + 1)).decode()
        with pytest.raises(ValueError, match="too large"):
            validate_script_criteria({"script": huge})

    def test_unsupported_runtime(self) -> None:
        with pytest.raises(ValueError, match="Unsupported runtime"):
            validate_script_criteria({
                "script": _b64("print('hi')"),
                "runtime": "haskell:9.8",
            })

    def test_default_runtime(self) -> None:
        """No runtime specified should be valid (defaults to python:3.13)."""
        validate_script_criteria({"script": _b64("print('ok')")})

    def test_timeout_too_large(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            validate_script_criteria({
                "script": _b64("print('ok')"),
                "timeout_seconds": 9999,
            })

    def test_timeout_negative(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            validate_script_criteria({
                "script": _b64("print('ok')"),
                "timeout_seconds": -1,
            })

    def test_memory_too_large(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            validate_script_criteria({
                "script": _b64("print('ok')"),
                "memory_limit_mb": 9999,
            })

    def test_all_allowed_runtimes(self) -> None:
        for runtime in ALLOWED_RUNTIMES:
            validate_script_criteria({
                "script": _b64("print('ok')"),
                "runtime": runtime,
            })


class TestSandboxResult:
    def test_to_dict_truncates_output(self) -> None:
        result = SandboxResult(
            passed=True,
            exit_code=0,
            stdout="x" * 5000,
            stderr="y" * 5000,
            timed_out=False,
        )
        d = result.to_dict()
        assert len(d["stdout"]) == 2000
        assert len(d["stderr"]) == 2000

    def test_to_dict_fields(self) -> None:
        result = SandboxResult(
            passed=False, exit_code=1, stdout="out", stderr="err",
            timed_out=True, error="something",
        )
        d = result.to_dict()
        assert d["passed"] is False
        assert d["exit_code"] == 1
        assert d["timed_out"] is True
        assert d["error"] == "something"


# ---------------------------------------------------------------------------
# Docker integration tests — require Docker daemon
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    import os
    import shutil
    if os.environ.get("CI"):
        return False  # GitHub Actions Docker lacks sandbox permissions
    return shutil.which("docker") is not None


docker = pytest.mark.skipif(not _docker_available(), reason="Docker sandbox not available")


class TestSandboxEarlyErrors:
    """Tests that fail before container creation — no Docker needed."""

    @pytest.mark.asyncio
    async def test_unsupported_runtime(self) -> None:
        result = await run_script_in_sandbox(
            script_b64=_b64("print('hi')"),
            deliverable={},
            runtime="cobol:85",
        )
        assert not result.passed
        assert result.error is not None
        assert "Unsupported runtime" in result.error

    @pytest.mark.asyncio
    async def test_script_too_large(self) -> None:
        huge = base64.b64encode(b"x" * (MAX_SCRIPT_SIZE_BYTES + 1)).decode()
        result = await run_script_in_sandbox(
            script_b64=huge,
            deliverable={},
        )
        assert not result.passed
        assert result.error is not None
        assert "too large" in result.error.lower()


@docker
class TestSandboxExecution:
    @pytest.mark.asyncio
    async def test_passing_python_script(self) -> None:
        """Script reads result.json, validates, exits 0."""
        script = """
import json, sys
with open('/input/result.json') as f:
    data = json.load(f)
if not isinstance(data, list):
    print("Expected a list", file=sys.stderr)
    sys.exit(1)
if len(data) < 3:
    print(f"Expected >= 3 items, got {len(data)}", file=sys.stderr)
    sys.exit(1)
print(f"OK: {len(data)} items")
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable=[1, 2, 3, 4, 5],
            runtime="python:3.13",
            timeout_seconds=30,
        )
        assert result.passed
        assert result.exit_code == 0
        assert "5 items" in result.stdout
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_failing_python_script(self) -> None:
        """Script finds bad data, exits 1."""
        script = """
import json, sys
with open('/input/result.json') as f:
    data = json.load(f)
if len(data) < 100:
    print(f"Need 100+ items, got {len(data)}", file=sys.stderr)
    sys.exit(1)
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable=[1, 2, 3],
            runtime="python:3.13",
            timeout_seconds=30,
        )
        assert not result.passed
        assert result.exit_code != 0
        assert "Need 100+" in result.stderr

    @pytest.mark.asyncio
    async def test_script_reads_complex_json(self) -> None:
        """Script can parse complex nested deliverable."""
        script = """
import json, sys
with open('/input/result.json') as f:
    data = json.load(f)
# Validate structure
for record in data['records']:
    if 'name' not in record or 'score' not in record:
        print(f"Missing fields in {record}", file=sys.stderr)
        sys.exit(1)
    if record['score'] < 0:
        print(f"Negative score: {record}", file=sys.stderr)
        sys.exit(1)
print(f"All {len(data['records'])} records valid")
"""
        deliverable = {
            "records": [
                {"name": "Alice", "score": 95},
                {"name": "Bob", "score": 87},
                {"name": "Charlie", "score": 92},
            ]
        }
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable=deliverable,
            runtime="python:3.13",
        )
        assert result.passed
        assert "3 records valid" in result.stdout

    @pytest.mark.asyncio
    async def test_bash_script(self) -> None:
        """Bash runtime works."""
        script = """#!/bin/bash
# Check that result.json exists and is valid JSON
if [ ! -f /input/result.json ]; then
    echo "No result.json found" >&2
    exit 1
fi
# Use grep to check for expected content
if grep -q '"status"' /input/result.json; then
    echo "Found status field"
    exit 0
else
    echo "Missing status field" >&2
    exit 1
fi
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable={"status": "complete", "data": [1, 2, 3]},
            runtime="bash",
        )
        assert result.passed
        assert "Found status field" in result.stdout

    @pytest.mark.asyncio
    async def test_network_isolation(self) -> None:
        """Container cannot make network requests."""
        script = """
import urllib.request, sys
try:
    urllib.request.urlopen('https://example.com', timeout=3)
    print("SECURITY FAILURE: network access allowed!", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Network correctly blocked: {e}")
    sys.exit(0)
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable={},
            runtime="python:3.13",
            timeout_seconds=15,
        )
        assert result.passed
        assert "correctly blocked" in result.stdout

    @pytest.mark.asyncio
    async def test_cannot_write_to_input(self) -> None:
        """Container cannot modify input files (read-only mount)."""
        script = """
import sys
try:
    with open('/input/result.json', 'w') as f:
        f.write('TAMPERED')
    print("SECURITY FAILURE: wrote to read-only mount!", file=sys.stderr)
    sys.exit(1)
except PermissionError:
    print("Correctly prevented write to /input")
    sys.exit(0)
except OSError as e:
    print(f"Correctly prevented write: {e}")
    sys.exit(0)
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable={"safe": True},
            runtime="python:3.13",
        )
        assert result.passed

    @pytest.mark.asyncio
    async def test_read_only_filesystem(self) -> None:
        """Container has read-only root filesystem."""
        script = """
import sys
try:
    with open('/etc/evil', 'w') as f:
        f.write('TAMPERED')
    print("SECURITY FAILURE: wrote to root filesystem!", file=sys.stderr)
    sys.exit(1)
except (PermissionError, OSError):
    print("Root filesystem is read-only")
    sys.exit(0)
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable={},
            runtime="python:3.13",
        )
        assert result.passed

    @pytest.mark.asyncio
    async def test_timeout_kills_container(self) -> None:
        """Script that runs too long gets killed."""
        script = """
import time
time.sleep(300)
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable={},
            runtime="python:3.13",
            timeout_seconds=3,
        )
        assert not result.passed
        assert result.timed_out

    @pytest.mark.asyncio
    async def test_script_syntax_error(self) -> None:
        """Script with syntax error should fail (non-zero exit)."""
        script = "def broken(\n  this is not python"
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable={},
            runtime="python:3.13",
        )
        assert not result.passed
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_memory_limit(self) -> None:
        """Script that tries to allocate too much memory gets killed."""
        script = """
# Try to allocate way more than 256MB
data = []
for i in range(100_000_000):
    data.append(b'x' * 1000)
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable={},
            runtime="python:3.13",
            memory_limit_mb=64,  # Low limit to trigger OOM faster
            timeout_seconds=15,
        )
        assert not result.passed

    @pytest.mark.asyncio
    async def test_no_privilege_escalation(self) -> None:
        """Container runs as non-root and cannot escalate."""
        script = """
import os, sys
uid = os.getuid()
if uid == 0:
    print("SECURITY FAILURE: running as root!", file=sys.stderr)
    sys.exit(1)
print(f"Running as UID {uid}")
sys.exit(0)
"""
        result = await run_script_in_sandbox(
            script_b64=_b64(script),
            deliverable={},
            runtime="python:3.13",
        )
        assert result.passed
        assert "UID" in result.stdout
        assert "root" not in result.stdout
