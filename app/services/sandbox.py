"""Docker-based sandbox for executing arbitrary acceptance criteria scripts.

Scripts run in isolated containers with:
- No network access (--network=none)
- No filesystem beyond mounted input
- Memory limit (default 256MB)
- CPU limit (default 1 CPU)
- Time limit (default 60s per script, enforced by Docker)
- Read-only root filesystem
- No privilege escalation
- Dropped capabilities

The deliverable is mounted as /input/result.json (read-only).
The script is mounted as /input/verify (read-only, executable).
Exit code 0 = pass, non-zero = fail.
stdout/stderr captured for audit.
"""

import asyncio
import base64
import json
import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Allowed runtimes — maps to Docker images
ALLOWED_RUNTIMES: dict[str, str] = {
    "python:3.13": "python:3.13-slim",
    "python:3.12": "python:3.12-slim",
    "node:20": "node:20-slim",
    "node:22": "node:22-slim",
    "bash": "bash:5",
    "ruby:3.3": "ruby:3.3-slim",
}

# Defaults
DEFAULT_RUNTIME = "python:3.13"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MEMORY_LIMIT_MB = 256
MAX_TIMEOUT_SECONDS = 300
MAX_MEMORY_LIMIT_MB = 512
MAX_SCRIPT_SIZE_BYTES = 1_048_576  # 1MB
MAX_OUTPUT_CAPTURE_BYTES = 65_536  # 64KB stdout/stderr capture


@dataclass
class SandboxResult:
    """Result of running a script in a sandbox container."""
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:2000],  # Truncate for API response
            "stderr": self.stderr[:2000],
            "timed_out": self.timed_out,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "error": self.error,
        }


def validate_script_criteria(criteria: dict) -> None:
    """Validate script-based acceptance criteria before job creation.

    Raises ValueError if criteria are invalid.
    """
    script_b64 = criteria.get("script")
    if not script_b64:
        raise ValueError("Script-based criteria must include 'script' field (base64-encoded)")

    # Validate base64
    try:
        script_bytes = base64.b64decode(script_b64)
    except Exception:
        raise ValueError("'script' must be valid base64-encoded content")

    if len(script_bytes) > MAX_SCRIPT_SIZE_BYTES:
        raise ValueError(f"Script too large: {len(script_bytes)} bytes (max {MAX_SCRIPT_SIZE_BYTES})")

    # Validate runtime
    runtime = criteria.get("runtime", DEFAULT_RUNTIME)
    if runtime not in ALLOWED_RUNTIMES:
        raise ValueError(f"Unsupported runtime: {runtime}. Allowed: {list(ALLOWED_RUNTIMES.keys())}")

    # Validate timeout
    timeout = criteria.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("timeout_seconds must be a positive number")
    if timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds cannot exceed {MAX_TIMEOUT_SECONDS}")

    # Validate memory limit
    memory = criteria.get("memory_limit_mb", DEFAULT_MEMORY_LIMIT_MB)
    if not isinstance(memory, (int, float)) or memory <= 0:
        raise ValueError("memory_limit_mb must be a positive number")
    if memory > MAX_MEMORY_LIMIT_MB:
        raise ValueError(f"memory_limit_mb cannot exceed {MAX_MEMORY_LIMIT_MB}")


async def run_script_in_sandbox(
    script_b64: str,
    deliverable: Any,
    runtime: str = DEFAULT_RUNTIME,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
) -> SandboxResult:
    """Execute a verification script against a deliverable in a Docker sandbox.

    Args:
        script_b64: Base64-encoded verification script.
        deliverable: The job deliverable (will be JSON-serialized to /input/result.json).
        runtime: Runtime identifier (must be in ALLOWED_RUNTIMES).
        timeout_seconds: Max execution time.
        memory_limit_mb: Max memory for the container.

    Returns:
        SandboxResult with pass/fail, output, and metadata.
    """
    if runtime not in ALLOWED_RUNTIMES:
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False, error=f"Unsupported runtime: {runtime}",
        )

    # Decode script
    try:
        script_bytes = base64.b64decode(script_b64)
    except Exception as e:
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False, error=f"Invalid base64 script: {e}",
        )

    if len(script_bytes) > MAX_SCRIPT_SIZE_BYTES:
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False, error=f"Script too large: {len(script_bytes)} bytes",
        )

    docker_image = ALLOWED_RUNTIMES[runtime]
    container_name = f"verify-{uuid.uuid4().hex[:12]}"

    # Create temp directory with input files
    with tempfile.TemporaryDirectory(prefix="sandbox-") as tmpdir:
        tmppath = Path(tmpdir)

        # Write deliverable as JSON
        result_path = tmppath / "result.json"
        result_path.write_text(json.dumps(deliverable, default=str))

        # Write script (make executable)
        script_path = tmppath / "verify"
        script_path.write_bytes(script_bytes)
        script_path.chmod(0o555)

        # Determine entrypoint based on runtime
        if runtime.startswith("python"):
            cmd = ["python", "/input/verify"]
        elif runtime.startswith("node"):
            cmd = ["node", "/input/verify"]
        elif runtime == "bash":
            cmd = ["bash", "/input/verify"]
        elif runtime.startswith("ruby"):
            cmd = ["ruby", "/input/verify"]
        else:
            cmd = ["/input/verify"]

        # Build docker command with security constraints
        docker_cmd = [
            "docker", "run",
            "--rm",
            "--name", container_name,
            # Network isolation
            "--network=none",
            # Resource limits
            f"--memory={memory_limit_mb}m",
            "--memory-swap", f"{memory_limit_mb}m",  # No swap
            "--cpus=1",
            "--pids-limit=256",
            # Security
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            # Temp filesystem for runtime needs (e.g., Python bytecode)
            "--tmpfs=/tmp:rw,noexec,nosuid,size=32m",
            # Mount input read-only
            "-v", f"{tmppath}:/input:ro",
            # Non-root user (use numeric UID to avoid user lookup issues)
            "--user=65534:65534",
            # Image and command
            docker_image,
            *cmd,
        ]

        try:
            import time as _time
            _t0 = _time.monotonic()

            process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds + 5,  # Docker timeout + grace
                )
            except asyncio.TimeoutError:
                # Kill the container if it's still running
                kill_proc = await asyncio.create_subprocess_exec(
                    "docker", "kill", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
                # Also wait for the process to clean up
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()

                _elapsed = _time.monotonic() - _t0
                return SandboxResult(
                    passed=False,
                    exit_code=-1,
                    stdout="",
                    stderr="Execution timed out",
                    timed_out=True,
                    elapsed_seconds=_elapsed,
                )

            _elapsed = _time.monotonic() - _t0
            stdout = stdout_bytes[:MAX_OUTPUT_CAPTURE_BYTES].decode("utf-8", errors="replace")
            stderr = stderr_bytes[:MAX_OUTPUT_CAPTURE_BYTES].decode("utf-8", errors="replace")
            exit_code = process.returncode or 0

            return SandboxResult(
                passed=(exit_code == 0),
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
                elapsed_seconds=_elapsed,
            )

        except FileNotFoundError:
            return SandboxResult(
                passed=False, exit_code=-1, stdout="", stderr="",
                timed_out=False, error="Docker not available",
            )
        except Exception as e:
            logger.exception("Sandbox execution failed")
            return SandboxResult(
                passed=False, exit_code=-1, stdout="", stderr="",
                timed_out=False, error=f"Sandbox error: {e}",
            )
