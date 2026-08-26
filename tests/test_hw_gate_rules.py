from __future__ import annotations

import math

import pytest

from hw_gate.rules import EvidenceError, Verdict, evaluate_rule


def test_exact_rule_accepts_equal_value() -> None:
    """Changing exact equality to a tolerance must fail this test."""
    result = evaluate_rule(1024, "bps", {"value": 1024, "unit": "bps", "rule": {"kind": "exact"}})

    assert result == {
        "baseline_value": 1024,
        "rule": {"kind": "exact", "reference": 1024},
        "delta": {"absolute": 0, "percent": 0.0},
        "verdict": "PASS",
    }


@pytest.mark.parametrize("value", [0, 10])
def test_range_rule_includes_both_boundaries(value: int) -> None:
    """Changing range comparisons from inclusive to exclusive must fail."""
    result = evaluate_rule(
        value,
        "count",
        {"value": 5, "unit": "count", "rule": {"kind": "range", "min": 0, "max": 10}},
    )

    assert result["verdict"] == "PASS"


def test_relative_rule_preserves_full_precision_delta() -> None:
    """Rounding before rule evaluation or output must fail this test."""
    result = evaluate_rule(
        1025,
        "bps",
        {"value": 1024, "unit": "bps", "rule": {"kind": "relative", "max_percent_delta": 0.1}},
    )

    assert result == {
        "baseline_value": 1024,
        "rule": {"kind": "relative", "reference": 1024, "max_percent_delta": 0.1},
        "delta": {"absolute": 1, "percent": 0.09765625},
        "verdict": "PASS",
    }


def test_absolute_rule_fails_outside_inclusive_bound() -> None:
    """Ignoring an absolute bound must fail this test."""
    result = evaluate_rule(
        1035,
        "bps",
        {"value": 1024, "unit": "bps", "rule": {"kind": "absolute", "max_delta": 10}},
    )

    assert result["verdict"] == "FAIL"


def test_relative_rule_rejects_zero_reference() -> None:
    """Dividing by zero or silently accepting a relative zero baseline must fail."""
    with pytest.raises(EvidenceError, match="non-zero"):
        evaluate_rule(1, "bps", {"value": 0, "unit": "bps", "rule": {"kind": "relative", "max_percent_delta": 5}})


@pytest.mark.parametrize("value", [True, "1024", math.nan, math.inf, -math.inf])
def test_rule_rejects_non_finite_or_non_numeric_observation(value: object) -> None:
    """Accepting booleans, strings, or non-finite observations must fail."""
    with pytest.raises(EvidenceError):
        evaluate_rule(value, "bps", {"value": 1024, "unit": "bps", "rule": {"kind": "exact"}})  # type: ignore[arg-type]


def test_rule_rejects_unit_mismatch() -> None:
    """Comparing values with incompatible units must fail."""
    with pytest.raises(EvidenceError, match="unit"):
        evaluate_rule(1024, "fps", {"value": 1024, "unit": "bps", "rule": {"kind": "exact"}})


def test_verdict_is_string_enum_for_json_compatibility() -> None:
    """Changing verdict values away from JSON-compatible strings must fail."""
    assert Verdict.PASS == "PASS"
