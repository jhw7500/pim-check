from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, Union


Number = Union[int, float]


class EvidenceError(ValueError):
    """Raised when evidence cannot be safely evaluated."""


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    BUSY = "BUSY"
    STALE = "STALE"


def _finite_number(value: object, field: str) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EvidenceError("{0} must be a finite numeric value".format(field))
    return value


def _rule_number(rule: Dict[str, Any], name: str) -> Number:
    return _finite_number(rule.get(name), "rule.{0}".format(name))


def evaluate_rule(value: Number, unit: str, baseline_metric: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate a finite observation against one normalized numeric rule."""
    observed = _finite_number(value, "value")
    if not isinstance(unit, str) or not unit:
        raise EvidenceError("unit must be a non-empty string")
    if not isinstance(baseline_metric, dict):
        raise EvidenceError("baseline metric must be a mapping")
    if baseline_metric.get("unit") != unit:
        raise EvidenceError("unit mismatch")

    baseline_value = _finite_number(baseline_metric.get("value"), "baseline value")
    source_rule = baseline_metric.get("rule")
    if not isinstance(source_rule, dict):
        raise EvidenceError("baseline rule must be a mapping")
    kind = source_rule.get("kind")
    if not isinstance(kind, str):
        raise EvidenceError("rule.kind must be a string")

    reference = _finite_number(source_rule.get("reference", baseline_value), "rule.reference")
    absolute_delta = observed - reference
    if reference == 0:
        if kind == "relative":
            raise EvidenceError("relative rule requires a non-zero reference")
        percent_delta = 0.0 if absolute_delta == 0 else 0.0
    else:
        percent_delta = absolute_delta / reference * 100.0

    rule: Dict[str, Any] = {"kind": kind}
    if kind == "exact":
        rule["reference"] = reference
        passed = observed == reference
    elif kind == "range":
        lower = _rule_number(source_rule, "min")
        upper = _rule_number(source_rule, "max")
        if lower > upper:
            raise EvidenceError("range minimum exceeds maximum")
        rule.update({"min": lower, "max": upper})
        passed = lower <= observed <= upper
    elif kind == "relative":
        bound = _rule_number(source_rule, "max_percent_delta")
        if bound < 0:
            raise EvidenceError("relative bound must be non-negative")
        rule.update({"reference": reference, "max_percent_delta": bound})
        passed = abs(percent_delta) <= bound
    elif kind == "absolute":
        bound = _rule_number(source_rule, "max_delta")
        if bound < 0:
            raise EvidenceError("absolute bound must be non-negative")
        rule.update({"reference": reference, "max_delta": bound})
        passed = abs(absolute_delta) <= bound
    else:
        raise EvidenceError("unsupported rule kind: {0}".format(kind))

    return {
        "baseline_value": baseline_value,
        "rule": rule,
        "delta": {"absolute": absolute_delta, "percent": percent_delta},
        "verdict": Verdict.PASS.value if passed else Verdict.FAIL.value,
    }
