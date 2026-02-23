"""Sandboxed acceptance criteria test runner.

Runs test suites against job deliverables. In production, each test runs in an
isolated Docker container with no network, no FS, 256MB memory, 60s timeout.
For v1, we run tests in-process with restricted evaluation.
"""

import ast
import hashlib
import re
from decimal import Decimal
from typing import Any

import jsonschema

# Safe builtins for assertion expressions
_SAFE_BUILTINS = {
    "True": True,
    "False": False,
    "None": None,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
}

# Forbidden AST nodes in assertion expressions
_FORBIDDEN_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Global,
    ast.Nonlocal,
    ast.Delete,
    ast.Try,
    ast.Raise,
    ast.With,
    ast.AsyncWith,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
)


def _validate_expression(expr: str) -> None:
    """Validate that an assertion expression is safe to evaluate."""
    if len(expr) > 500:
        raise ValueError("Expression too long (max 500 chars)")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e}")

    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODES):
            raise ValueError(f"Forbidden construct: {type(node).__name__}")
        # Block attribute access to dunder methods
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError(f"Access to dunder attributes forbidden: {node.attr}")
        # Block calls to exec/eval/compile/open etc
        if isinstance(node, ast.Name) and node.id in (
            "exec", "eval", "compile", "open", "input", "__import__",
            "getattr", "setattr", "delattr", "globals", "locals", "vars",
            "breakpoint", "exit", "quit",
        ):
            raise ValueError(f"Forbidden builtin: {node.id}")


def _resolve_jsonpath(data: Any, path: str) -> Any:
    """Simple JSONPath resolver. Supports $ (root) and .field / [index]."""
    if path == "$":
        return data
    current = data
    # Strip leading $
    path = path.lstrip("$")
    for part in re.findall(r'\.(\w+)|\[(\d+)\]', path):
        field, index = part
        if field:
            if isinstance(current, dict):
                current = current[field]
            else:
                raise ValueError(f"Cannot access field '{field}' on non-dict")
        elif index:
            current = current[int(index)]
    return current


class TestResult:
    """Result of a single test."""

    def __init__(self, test_id: str, passed: bool, message: str = "") -> None:
        self.test_id = test_id
        self.passed = passed
        self.message = message

    def to_dict(self) -> dict:
        return {"test_id": self.test_id, "passed": self.passed, "message": self.message}


class SuiteResult:
    """Result of running the full test suite."""

    def __init__(self, results: list[TestResult], threshold: str | dict, sandbox_result: Any | None = None) -> None:
        self.results = results
        self.threshold = threshold
        self.sandbox_result = sandbox_result

    @property
    def passed(self) -> bool:
        if self.threshold == "all":
            return all(r.passed for r in self.results)
        if self.threshold == "majority":
            passed_count = sum(1 for r in self.results if r.passed)
            return passed_count > len(self.results) / 2
        if isinstance(self.threshold, dict) and "min_pass" in self.threshold:
            passed_count = sum(1 for r in self.results if r.passed)
            return passed_count >= self.threshold["min_pass"]
        return all(r.passed for r in self.results)

    def to_dict(self) -> dict:
        d = {
            "passed": self.passed,
            "threshold": self.threshold,
            "results": [r.to_dict() for r in self.results],
            "summary": f"{sum(1 for r in self.results if r.passed)}/{len(self.results)} passed",
        }
        if self.sandbox_result is not None:
            d["sandbox"] = self.sandbox_result.to_dict()
        return d


def _run_json_schema_test(test_id: str, output: Any, params: dict) -> TestResult:
    """Validate output against a JSON Schema."""
    try:
        jsonschema.validate(instance=output, schema=params["schema"])
        return TestResult(test_id, True)
    except jsonschema.ValidationError as e:
        return TestResult(test_id, False, str(e.message)[:200])
    except Exception as e:
        return TestResult(test_id, False, f"Schema validation error: {e}")


def _run_count_gte_test(test_id: str, output: Any, params: dict) -> TestResult:
    """Check array at path has >= N items."""
    try:
        data = _resolve_jsonpath(output, params.get("path", "$"))
        if not isinstance(data, list):
            return TestResult(test_id, False, "Target is not an array")
        count = len(data)
        min_count = params["min_count"]
        if count >= min_count:
            return TestResult(test_id, True, f"Count {count} >= {min_count}")
        return TestResult(test_id, False, f"Count {count} < {min_count}")
    except Exception as e:
        return TestResult(test_id, False, str(e))


def _run_count_lte_test(test_id: str, output: Any, params: dict) -> TestResult:
    """Check array at path has <= N items."""
    try:
        data = _resolve_jsonpath(output, params.get("path", "$"))
        if not isinstance(data, list):
            return TestResult(test_id, False, "Target is not an array")
        count = len(data)
        max_count = params["max_count"]
        if count <= max_count:
            return TestResult(test_id, True, f"Count {count} <= {max_count}")
        return TestResult(test_id, False, f"Count {count} > {max_count}")
    except Exception as e:
        return TestResult(test_id, False, str(e))


def _run_assertion_test(test_id: str, output: Any, params: dict) -> TestResult:
    """Run a sandboxed Python assertion expression."""
    expr = params.get("expression", "")
    try:
        _validate_expression(expr)
        # Create restricted namespace
        namespace = {"output": output, "__builtins__": _SAFE_BUILTINS}
        result = eval(compile(ast.parse(expr, mode="eval"), "<assertion>", "eval"), namespace)
        if result:
            return TestResult(test_id, True)
        return TestResult(test_id, False, f"Assertion failed: {expr}")
    except ValueError as e:
        return TestResult(test_id, False, f"Invalid expression: {e}")
    except Exception as e:
        return TestResult(test_id, False, f"Assertion error: {e}")


def _run_contains_test(test_id: str, output: Any, params: dict) -> TestResult:
    """Check if output contains a substring or matches a regex."""
    try:
        output_str = str(output)
        pattern = params.get("pattern", "")
        is_regex = params.get("is_regex", False)
        if is_regex:
            if re.search(pattern, output_str):
                return TestResult(test_id, True)
            return TestResult(test_id, False, f"Pattern '{pattern}' not found")
        if pattern in output_str:
            return TestResult(test_id, True)
        return TestResult(test_id, False, f"Substring '{pattern}' not found")
    except Exception as e:
        return TestResult(test_id, False, str(e))


def _run_latency_lte_test(test_id: str, output: Any, params: dict) -> TestResult:
    """Check that delivery latency is within max_seconds.

    The output should contain a '_delivery_meta' dict with 'started_at' and 'delivered_at'
    ISO timestamps, or the params should include 'actual_seconds'.
    """
    try:
        actual = params.get("actual_seconds")
        if actual is None:
            # Try to compute from output metadata
            meta = output.get("_delivery_meta", {}) if isinstance(output, dict) else {}
            if "started_at" in meta and "delivered_at" in meta:
                from datetime import datetime, timezone
                started = datetime.fromisoformat(meta["started_at"])
                delivered = datetime.fromisoformat(meta["delivered_at"])
                actual = (delivered - started).total_seconds()
            else:
                return TestResult(test_id, False, "Cannot determine delivery latency")

        max_seconds = params["max_seconds"]
        if actual <= max_seconds:
            return TestResult(test_id, True, f"Latency {actual}s <= {max_seconds}s")
        return TestResult(test_id, False, f"Latency {actual}s > {max_seconds}s")
    except Exception as e:
        return TestResult(test_id, False, str(e))


def _run_http_status_test(test_id: str, output: Any, params: dict) -> TestResult:
    """Check if a URL in the output returns an expected HTTP status.

    Note: In production this runs in a sandboxed container with network access.
    For v1 in-process runner, we skip the actual HTTP call and check the output metadata.
    """
    try:
        expected = params.get("expected_status", 200)
        # Check if output contains a URL result with status
        if isinstance(output, dict):
            actual_status = output.get("http_status") or output.get("status_code")
            if actual_status is not None:
                if int(actual_status) == expected:
                    return TestResult(test_id, True, f"HTTP status {actual_status} == {expected}")
                return TestResult(test_id, False, f"HTTP status {actual_status} != {expected}")
        return TestResult(test_id, False, "No http_status or status_code in output")
    except Exception as e:
        return TestResult(test_id, False, str(e))


def _run_checksum_test(test_id: str, output: Any, params: dict) -> TestResult:
    """Verify SHA-256 of output matches expected hash."""
    try:
        import json as json_mod
        output_bytes = json_mod.dumps(output, sort_keys=True).encode()
        actual_hash = hashlib.sha256(output_bytes).hexdigest()
        expected = params.get("expected_hash", "")
        if actual_hash == expected:
            return TestResult(test_id, True)
        return TestResult(test_id, False, f"Hash mismatch: {actual_hash[:16]}... != {expected[:16]}...")
    except Exception as e:
        return TestResult(test_id, False, str(e))


# Test type dispatcher
_TEST_RUNNERS = {
    "json_schema": _run_json_schema_test,
    "count_gte": _run_count_gte_test,
    "count_lte": _run_count_lte_test,
    "assertion": _run_assertion_test,
    "contains": _run_contains_test,
    "latency_lte": _run_latency_lte_test,
    "http_status": _run_http_status_test,
    "checksum": _run_checksum_test,
}


async def run_script_test(criteria: dict, output: Any) -> SuiteResult:
    """Run a script-based acceptance test in a Docker sandbox.

    The criteria dict must contain:
    - script: base64-encoded verification script
    - runtime: (optional) one of ALLOWED_RUNTIMES
    - timeout_seconds: (optional) max execution time
    - memory_limit_mb: (optional) max memory
    """
    from app.services.sandbox import run_script_in_sandbox, DEFAULT_RUNTIME, DEFAULT_TIMEOUT_SECONDS, DEFAULT_MEMORY_LIMIT_MB

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

    # Script-based criteria always use "all" threshold with a single test
    return SuiteResult(
        results=[test_result],
        threshold="all",
        sandbox_result=sandbox_result,
    )


def run_test_suite(criteria: dict, output: Any) -> SuiteResult:
    """Run a full acceptance criteria test suite against the deliverable output."""
    tests = criteria.get("tests", [])
    threshold = criteria.get("pass_threshold", "all")

    if not tests:
        return SuiteResult([], threshold)

    if len(tests) > 20:
        raise ValueError("Maximum 20 tests per suite")

    results: list[TestResult] = []
    for test in tests:
        test_id = test.get("test_id", "unknown")
        test_type = test.get("type", "")
        params = test.get("params", {})

        runner = _TEST_RUNNERS.get(test_type)
        if runner is None:
            results.append(TestResult(test_id, False, f"Unknown test type: {test_type}"))
            continue

        result = runner(test_id, output, params)
        results.append(result)

    return SuiteResult(results, threshold)
