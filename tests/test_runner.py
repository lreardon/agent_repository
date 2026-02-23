"""Tests for the acceptance criteria test runner (unit tests, no DB needed)."""

import pytest

from app.services.test_runner import run_test_suite, _validate_expression


class TestJsonSchema:
    def test_valid_schema(self) -> None:
        criteria = {
            "tests": [{
                "test_id": "schema_check",
                "type": "json_schema",
                "params": {
                    "schema": {
                        "type": "array",
                        "items": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
                    }
                }
            }],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, [{"name": "Alice"}, {"name": "Bob"}])
        assert result.passed

    def test_invalid_schema(self) -> None:
        criteria = {
            "tests": [{
                "test_id": "schema_check",
                "type": "json_schema",
                "params": {"schema": {"type": "array", "items": {"type": "string"}}}
            }],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, [1, 2, 3])
        assert not result.passed


class TestCountGte:
    def test_pass(self) -> None:
        criteria = {
            "tests": [{"test_id": "count", "type": "count_gte", "params": {"path": "$", "min_count": 3}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, [1, 2, 3, 4])
        assert result.passed

    def test_fail(self) -> None:
        criteria = {
            "tests": [{"test_id": "count", "type": "count_gte", "params": {"path": "$", "min_count": 10}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, [1, 2])
        assert not result.passed


class TestCountLte:
    def test_pass(self) -> None:
        criteria = {
            "tests": [{"test_id": "count", "type": "count_lte", "params": {"path": "$", "max_count": 5}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, [1, 2])
        assert result.passed


class TestAssertion:
    def test_simple_pass(self) -> None:
        criteria = {
            "tests": [{"test_id": "assert1", "type": "assertion", "params": {"expression": "len(output) > 0"}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, [1, 2, 3])
        assert result.passed

    def test_complex_expression(self) -> None:
        criteria = {
            "tests": [{
                "test_id": "no_nulls",
                "type": "assertion",
                "params": {"expression": "all(r['name'] is not None for r in output)"}
            }],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, [{"name": "Alice"}, {"name": "Bob"}])
        assert result.passed

    def test_fail(self) -> None:
        criteria = {
            "tests": [{"test_id": "assert1", "type": "assertion", "params": {"expression": "len(output) > 100"}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, [1, 2])
        assert not result.passed

    def test_forbidden_import(self) -> None:
        with pytest.raises(ValueError, match="Forbidden"):
            _validate_expression("__import__('os')")

    def test_forbidden_exec(self) -> None:
        with pytest.raises(ValueError, match="Forbidden"):
            _validate_expression("exec('print(1)')")

    def test_forbidden_dunder(self) -> None:
        with pytest.raises(ValueError, match="dunder"):
            _validate_expression("output.__class__")

    def test_expression_too_long(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            _validate_expression("x" * 501)


class TestContains:
    def test_substring(self) -> None:
        criteria = {
            "tests": [{"test_id": "has_key", "type": "contains", "params": {"pattern": "hello"}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, "hello world")
        assert result.passed

    def test_regex(self) -> None:
        criteria = {
            "tests": [{"test_id": "regex", "type": "contains", "params": {"pattern": r"\d{3}-\d{4}", "is_regex": True}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, "Call 555-1234")
        assert result.passed


class TestLatencyLte:
    def test_pass_with_actual_seconds(self) -> None:
        criteria = {
            "tests": [{"test_id": "lat", "type": "latency_lte", "params": {"max_seconds": 3600, "actual_seconds": 1200}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, {})
        assert result.passed

    def test_fail_with_actual_seconds(self) -> None:
        criteria = {
            "tests": [{"test_id": "lat", "type": "latency_lte", "params": {"max_seconds": 60, "actual_seconds": 120}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, {})
        assert not result.passed

    def test_no_latency_info(self) -> None:
        criteria = {
            "tests": [{"test_id": "lat", "type": "latency_lte", "params": {"max_seconds": 60}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, {})
        assert not result.passed


class TestHttpStatus:
    def test_pass(self) -> None:
        criteria = {
            "tests": [{"test_id": "http", "type": "http_status", "params": {"expected_status": 200}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, {"http_status": 200})
        assert result.passed

    def test_fail(self) -> None:
        criteria = {
            "tests": [{"test_id": "http", "type": "http_status", "params": {"expected_status": 200}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, {"http_status": 404})
        assert not result.passed

    def test_no_status(self) -> None:
        criteria = {
            "tests": [{"test_id": "http", "type": "http_status", "params": {"expected_status": 200}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, {"data": "no status here"})
        assert not result.passed


class TestChecksum:
    def test_matching_hash(self) -> None:
        import hashlib, json
        data = {"key": "value"}
        expected = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        criteria = {
            "tests": [{"test_id": "hash", "type": "checksum", "params": {"expected_hash": expected}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, data)
        assert result.passed

    def test_mismatched_hash(self) -> None:
        criteria = {
            "tests": [{"test_id": "hash", "type": "checksum", "params": {"expected_hash": "deadbeef"}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, {"key": "value"})
        assert not result.passed


class TestThresholds:
    def _make_criteria(self, threshold: str | dict) -> dict:
        return {
            "tests": [
                {"test_id": "t1", "type": "assertion", "params": {"expression": "True"}},
                {"test_id": "t2", "type": "assertion", "params": {"expression": "False"}},
                {"test_id": "t3", "type": "assertion", "params": {"expression": "True"}},
            ],
            "pass_threshold": threshold,
        }

    def test_all_threshold_fails(self) -> None:
        result = run_test_suite(self._make_criteria("all"), "data")
        assert not result.passed  # 2/3 pass, not all

    def test_majority_threshold_passes(self) -> None:
        result = run_test_suite(self._make_criteria("majority"), "data")
        assert result.passed  # 2/3 > 50%

    def test_min_pass_threshold(self) -> None:
        result = run_test_suite(self._make_criteria({"min_pass": 2}), "data")
        assert result.passed  # 2 >= 2

        result = run_test_suite(self._make_criteria({"min_pass": 3}), "data")
        assert not result.passed  # 2 < 3


class TestSuiteEdgeCases:
    def test_empty_tests(self) -> None:
        result = run_test_suite({"tests": [], "pass_threshold": "all"}, "data")
        assert result.passed

    def test_unknown_test_type(self) -> None:
        criteria = {
            "tests": [{"test_id": "bad", "type": "nonexistent", "params": {}}],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, "data")
        assert not result.passed

    def test_max_20_tests(self) -> None:
        criteria = {
            "tests": [{"test_id": f"t{i}", "type": "assertion", "params": {"expression": "True"}} for i in range(21)],
            "pass_threshold": "all",
        }
        with pytest.raises(ValueError, match="Maximum 20"):
            run_test_suite(criteria, "data")

    def test_suite_result_summary(self) -> None:
        criteria = {
            "tests": [
                {"test_id": "t1", "type": "assertion", "params": {"expression": "True"}},
                {"test_id": "t2", "type": "assertion", "params": {"expression": "False"}},
            ],
            "pass_threshold": "all",
        }
        result = run_test_suite(criteria, "data")
        d = result.to_dict()
        assert d["summary"] == "1/2 passed"
        assert d["passed"] is False
