"""Sandboxed acceptance criteria runner — script-based only.

All verification runs a user-supplied script in an isolated Docker container:
no network, read-only filesystem, enforced memory and timeout limits.
The script receives the deliverable at /input/result.json and must exit 0 to pass.
"""

from typing import Any


class TestResult:
    """Result of a single verification run."""

    def __init__(self, test_id: str, passed: bool, message: str = "") -> None:
        self.test_id = test_id
        self.passed = passed
        self.message = message

    def to_dict(self) -> dict:
        return {"test_id": self.test_id, "passed": self.passed, "message": self.message}


class SuiteResult:
    """Result of a verification run."""

    def __init__(self, results: list[TestResult], threshold: str = "all",
                 sandbox_result: Any | None = None) -> None:
        self.results = results
        self.threshold = threshold
        self.sandbox_result = sandbox_result

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def to_dict(self) -> dict:
        d: dict = {
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
            "summary": f"{sum(1 for r in self.results if r.passed)}/{len(self.results)} passed",
        }
        if self.sandbox_result is not None:
            d["sandbox"] = self.sandbox_result.to_dict()
        return d


async def run_script_test(criteria: dict, output: Any) -> SuiteResult:
    """Run a script-based acceptance test in a Docker sandbox.

    The criteria dict must contain:
    - script: base64-encoded verification script
    - runtime: (optional) one of ALLOWED_RUNTIMES, default python:3.13
    - timeout_seconds: (optional) max execution time, default 60
    - memory_limit_mb: (optional) max memory, default 256
    """
    from app.services.sandbox import (
        run_script_in_sandbox,
        DEFAULT_RUNTIME,
        DEFAULT_TIMEOUT_SECONDS,
        DEFAULT_MEMORY_LIMIT_MB,
    )

    sandbox_result = await run_script_in_sandbox(
        script_b64=criteria["script"],
        deliverable=output,
        runtime=criteria.get("runtime", DEFAULT_RUNTIME),
        timeout_seconds=criteria.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        memory_limit_mb=criteria.get("memory_limit_mb", DEFAULT_MEMORY_LIMIT_MB),
    )

    test_result = TestResult(
        test_id="script",
        passed=sandbox_result.passed,
        message=sandbox_result.stderr if not sandbox_result.passed else sandbox_result.stdout[:500],
    )

    return SuiteResult(
        results=[test_result],
        threshold="all",
        sandbox_result=sandbox_result,
    )
