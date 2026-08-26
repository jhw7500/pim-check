from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Union

from .rules import EvidenceError, _finite_number, evaluate_rule


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MODULE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOP_LEVEL_KEYS = {
    "schema_version",
    "baseline_version",
    "source_commit",
    "comparability",
    "target_identity",
    "gates",
    "calibration",
}
_COMPARABILITY_KEYS = {"board_id", "target_host", "bps_fixture", "encoder"}
_GATE_KEYS = {"adapter_schema_version", "comparability", "metrics"}
_METRIC_KEYS = {"value", "unit", "rule", "calibration_required"}
_IDENTITY_KEYS = {"id", "kind", "module", "path", "sha256", "version", "calibration_required"}
_CALIBRATION_KEYS = {"bps"}
_BPS_CALIBRATION_KEYS = {"source_run_ids", "samples"}
_BPS_SETPOINTS = (1024, 2048, 4096, 8192)
_MIXED_CHANNELS = {
    1: {1: (2, 665, 4447), 3: (1, 656, 4432)},
    2: {0: (3, 665, 4432), 2: (0, 656, 4447)},
    3: {0: (2, 665, 4447), 1: (1, 656, 4432)},
    4: {0: (2, 665, 4447), 1: (1, 656, 4432), 2: (3, 665, 4432), 3: (0, 656, 4447)},
}
_MIXED_MODE_MASKS = {1: (0, 0), 2: (0, 0), 3: (0, 3), 4: (3, 3)}


@dataclass(frozen=True)
class LoadedBaseline:
    """A validated baseline plus the digest of its exact committed bytes."""

    data: Dict[str, Any]
    sha256: str
    path: Path


def _expected_metric_values() -> Dict[str, Dict[str, Union[int, str]]]:
    bps: Dict[str, Union[int, str]] = {}
    for setpoint in _BPS_SETPOINTS:
        for assertion in ("target", "baseline"):
            bps["bps.ch0.{0}.{1}".format(setpoint, assertion)] = "bps"
    mixed: Dict[str, Union[int, str]] = {}
    for test_id, channels in _MIXED_CHANNELS.items():
        for bus, value in enumerate(_MIXED_MODE_MASKS[test_id], start=1):
            mixed["mixed_combo.test{0}.bus{1}.mode_mask".format(test_id, bus)] = value
        for channel, values in channels.items():
            for name, value in zip(("rotation", "ae", "awb"), values):
                mixed["mixed_combo.test{0}.ch{1}.{2}".format(test_id, channel, name)] = value
    return {"bps_quick": bps, "mixed_combo": mixed}


_EXPECTED_METRICS = _expected_metric_values()


def _require_mapping(value: object, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("{0} must be a mapping".format(field))
    return value


def _require_list(value: object, field: str) -> List[Any]:
    if not isinstance(value, list):
        raise EvidenceError("{0} must be a list".format(field))
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError("{0} must be a non-empty string".format(field))
    return value


def _require_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceError("{0} must be an integer".format(field))
    return value


def _reject_unknown(mapping: Mapping[str, Any], allowed: Iterable[str], field: str) -> None:
    unknown = set(mapping).difference(allowed)
    if unknown:
        raise EvidenceError("{0} contains unknown key: {1}".format(field, sorted(unknown)[0]))


def _validate_comparability(value: object, field: str, *, top_level: bool) -> Dict[str, Any]:
    comparability = _require_mapping(value, field)
    if top_level:
        _reject_unknown(comparability, _COMPARABILITY_KEYS, field)
        if set(comparability) != _COMPARABILITY_KEYS:
            raise EvidenceError("{0} must contain the complete comparability context".format(field))
    for key, item in comparability.items():
        _require_string(key, "{0} key".format(field))
        _require_string(item, "{0}.{1}".format(field, key))
    return comparability


def _validate_rule(rule_value: object, metric_value: object, field: str, calibration_required: bool) -> None:
    rule = _require_mapping(rule_value, field)
    kind = _require_string(rule.get("kind"), "{0}.kind".format(field))
    allowed_by_kind = {
        "exact": {"kind", "reference"},
        "range": {"kind", "reference", "min", "max"},
        "relative": {"kind", "reference", "max_percent_delta"},
        "absolute": {"kind", "reference", "max_delta"},
    }
    if kind not in allowed_by_kind:
        raise EvidenceError("{0}.kind is unsupported".format(field))
    _reject_unknown(rule, allowed_by_kind[kind], field)
    required_by_kind = {
        "exact": (),
        "range": ("min", "max"),
        "relative": ("max_percent_delta",),
        "absolute": ("max_delta",),
    }
    for name in required_by_kind[kind]:
        _finite_number(rule.get(name), "{0}.{1}".format(field, name))
    if "reference" in rule:
        _finite_number(rule["reference"], "{0}.reference".format(field))
    if kind == "range" and rule["min"] > rule["max"]:
        raise EvidenceError("{0} minimum exceeds maximum".format(field))
    if kind in {"relative", "absolute"}:
        bound_name = "max_percent_delta" if kind == "relative" else "max_delta"
        if rule[bound_name] < 0:
            raise EvidenceError("{0}.{1} must be non-negative".format(field, bound_name))
    if metric_value is not None:
        evaluate_rule(
            _finite_number(metric_value, "{0}.value".format(field)),
            "unit",
            {"value": metric_value, "unit": "unit", "rule": rule},
        )
    elif not calibration_required:
        raise EvidenceError("{0}.value is required".format(field))


def _validate_metric(metric_id: object, value: object, field: str, production: bool) -> None:
    _require_string(metric_id, "{0}.id".format(field))
    metric = _require_mapping(value, field)
    _reject_unknown(metric, _METRIC_KEYS, field)
    calibration_required = metric.get("calibration_required", False)
    if not isinstance(calibration_required, bool):
        raise EvidenceError("{0}.calibration_required must be a boolean".format(field))
    if calibration_required and production:
        raise EvidenceError("{0}.calibration_required is not allowed in production".format(field))
    if "unit" not in metric:
        raise EvidenceError("{0}.unit is required".format(field))
    _require_string(metric["unit"], "{0}.unit".format(field))
    if "rule" not in metric:
        raise EvidenceError("{0}.rule is required".format(field))
    metric_value = metric.get("value")
    if "value" in metric:
        _finite_number(metric_value, "{0}.value".format(field))
    _validate_rule(metric["rule"], metric_value, "{0}.rule".format(field), calibration_required)


def _validate_identity_claim(value: object, field: str, production: bool) -> str:
    claim = _require_mapping(value, field)
    _reject_unknown(claim, _IDENTITY_KEYS, field)
    identifier = _require_string(claim.get("id"), "{0}.id".format(field))
    if not _IDENTITY_ID_RE.fullmatch(identifier):
        raise EvidenceError("{0}.id is unsafe".format(field))
    kind = _require_string(claim.get("kind"), "{0}.kind".format(field))
    calibration_required = claim.get("calibration_required", False)
    if not isinstance(calibration_required, bool):
        raise EvidenceError("{0}.calibration_required must be a boolean".format(field))
    if calibration_required and production:
        raise EvidenceError("{0}.calibration_required is not allowed in production".format(field))
    required_by_kind = {
        "module_sha256": {"module", "sha256"},
        "module_version": {"module", "version"},
        "file_sha256": {"path", "sha256"},
    }
    if kind not in required_by_kind:
        raise EvidenceError("{0}.kind is unsupported".format(field))
    present_measurement_keys = set(claim).intersection({"module", "path", "sha256", "version"})
    if calibration_required:
        allowed_context = {"module"} if kind.startswith("module_") else {"path"}
        if not present_measurement_keys.issubset(allowed_context):
            raise EvidenceError("{0} calibration marker cannot include measured values".format(field))
    elif not required_by_kind[kind].issubset(claim):
        raise EvidenceError("{0} is missing required identity values".format(field))
    if "module" in claim and not _MODULE_RE.fullmatch(_require_string(claim["module"], "{0}.module".format(field))):
        raise EvidenceError("{0}.module is unsafe".format(field))
    if "path" in claim:
        path = _require_string(claim["path"], "{0}.path".format(field))
        if not path.startswith("/") or ".." in path.split("/"):
            raise EvidenceError("{0}.path is unsafe".format(field))
    if "sha256" in claim and not _SHA256_RE.fullmatch(_require_string(claim["sha256"], "{0}.sha256".format(field))):
        raise EvidenceError("{0}.sha256 must be a lowercase SHA-256".format(field))
    if "version" in claim:
        _require_string(claim["version"], "{0}.version".format(field))
    return identifier


def _validate_calibration(value: object) -> None:
    calibration = _require_mapping(value, "calibration")
    _reject_unknown(calibration, _CALIBRATION_KEYS, "calibration")
    bps = _require_mapping(calibration.get("bps"), "calibration.bps")
    _reject_unknown(bps, _BPS_CALIBRATION_KEYS, "calibration.bps")
    run_ids = _require_list(bps.get("source_run_ids"), "calibration.bps.source_run_ids")
    if len(run_ids) != len(set(_require_string(item, "calibration.bps.source_run_ids[]") for item in run_ids)):
        raise EvidenceError("calibration.bps.source_run_ids contains duplicates")
    samples = _require_mapping(bps.get("samples"), "calibration.bps.samples")
    for setpoint, sample_values in samples.items():
        _require_string(setpoint, "calibration.bps.samples key")
        for index, sample in enumerate(_require_list(sample_values, "calibration.bps.samples.{0}".format(setpoint))):
            _finite_number(sample, "calibration.bps.samples.{0}[{1}]".format(setpoint, index))


def _validate_committed_metric_inventory(gate_id: str, metrics: Dict[str, Any]) -> None:
    expected = _EXPECTED_METRICS[gate_id]
    if set(metrics) != set(expected):
        raise EvidenceError("gates.{0}.metrics does not match the committed metric inventory".format(gate_id))
    for metric_id, expected_value in expected.items():
        metric = metrics[metric_id]
        if expected_value == "bps":
            if metric["unit"] != "bps":
                raise EvidenceError("gates.{0}.metrics.{1}.unit must be bps".format(gate_id, metric_id))
            setpoint = int(metric_id.split(".")[2]) * 1000
            rule = metric["rule"]
            if metric_id.endswith(".target"):
                expected_rule = {"kind": "relative", "reference": setpoint, "max_percent_delta": 10}
                if metric.get("value") != setpoint or rule != expected_rule:
                    raise EvidenceError("gates.{0}.metrics.{1} must keep the BPS target rule".format(gate_id, metric_id))
            else:
                if rule.get("kind") != "relative" or rule.get("max_percent_delta") != 5:
                    raise EvidenceError("gates.{0}.metrics.{1} must keep the BPS baseline rule".format(gate_id, metric_id))
                if "value" in metric and rule.get("reference", metric["value"]) != metric["value"]:
                    raise EvidenceError("gates.{0}.metrics.{1}.rule reference must equal the baseline value".format(gate_id, metric_id))
        elif metric["unit"] != ("mode_mask" if metric_id.endswith(".mode_mask") else "register_word"):
            raise EvidenceError("gates.{0}.metrics.{1}.unit is not the committed unit".format(gate_id, metric_id))
        elif metric.get("value") != expected_value or metric["rule"] != {"kind": "exact"}:
            raise EvidenceError("gates.{0}.metrics.{1} must keep the exact mixed-combo policy".format(gate_id, metric_id))


def validate_baseline(data: object, production: bool = True) -> None:
    """Validate the committed baseline schema and reject policy-weakening input."""
    baseline = _require_mapping(data, "baseline")
    _reject_unknown(baseline, _TOP_LEVEL_KEYS, "baseline")
    if _require_integer(baseline.get("schema_version"), "schema_version") != 1:
        raise EvidenceError("schema_version must be 1")
    _require_string(baseline.get("baseline_version"), "baseline_version")
    source_commit = _require_string(baseline.get("source_commit"), "source_commit")
    if not _COMMIT_RE.fullmatch(source_commit):
        raise EvidenceError("source_commit must be a lowercase git commit SHA")
    _validate_comparability(baseline.get("comparability"), "comparability", top_level=True)

    identities = _require_list(baseline.get("target_identity"), "target_identity")
    if production and not identities:
        raise EvidenceError("target_identity must contain at least one identity claim")
    identity_ids = [_validate_identity_claim(item, "target_identity[{0}]".format(index), production) for index, item in enumerate(identities)]
    if len(identity_ids) != len(set(identity_ids)):
        raise EvidenceError("duplicate target_identity id")

    gates = _require_mapping(baseline.get("gates"), "gates")
    if set(gates) != set(_EXPECTED_METRICS):
        raise EvidenceError("gates must match the committed gate inventory")
    for gate_id, gate_value in gates.items():
        _require_string(gate_id, "gates key")
        gate = _require_mapping(gate_value, "gates.{0}".format(gate_id))
        _reject_unknown(gate, _GATE_KEYS, "gates.{0}".format(gate_id))
        if _require_integer(gate.get("adapter_schema_version"), "gates.{0}.adapter_schema_version".format(gate_id)) != 1:
            raise EvidenceError("gates.{0}.adapter_schema_version must be 1".format(gate_id))
        _validate_comparability(gate.get("comparability"), "gates.{0}.comparability".format(gate_id), top_level=False)
        metrics = _require_mapping(gate.get("metrics"), "gates.{0}.metrics".format(gate_id))
        if not metrics:
            raise EvidenceError("gates.{0}.metrics must not be empty".format(gate_id))
        for metric_id, metric in metrics.items():
            _validate_metric(metric_id, metric, "gates.{0}.metrics.{1}".format(gate_id, metric_id), production)
        _validate_committed_metric_inventory(gate_id, metrics)
    _validate_calibration(baseline.get("calibration"))


def baseline_sha256(path: Union[str, Path]) -> str:
    """Return the SHA-256 of the exact baseline bytes, never re-serialized JSON."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise EvidenceError("baseline is unavailable: {0}".format(path)) from exc


def load_baseline(path: Union[str, Path]) -> LoadedBaseline:
    """Load one production baseline and bind it to its committed byte digest."""
    baseline_path = Path(path)
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError("baseline is unavailable or malformed: {0}".format(baseline_path)) from exc
    validate_baseline(data, production=True)
    return LoadedBaseline(data=data, sha256=baseline_sha256(baseline_path), path=baseline_path)


def assert_gate_coverage(raw_gate: object, baseline_gate: object) -> None:
    """Require exact metric IDs, units, and comparability before rule evaluation."""
    raw = _require_mapping(raw_gate, "raw gate")
    baseline = _require_mapping(baseline_gate, "baseline gate")
    raw_comparability = raw.get("comparability", {})
    baseline_comparability = baseline.get("comparability", {})
    if not isinstance(raw_comparability, dict) or not isinstance(baseline_comparability, dict):
        raise EvidenceError("gate comparability must be a mapping")
    if raw_comparability != baseline_comparability:
        raise EvidenceError("gate comparability mismatch")
    raw_metrics = _require_list(raw.get("metrics"), "raw gate.metrics")
    baseline_metrics = baseline.get("metrics")
    if isinstance(baseline_metrics, dict):
        expected_units = {
            _require_string(metric_id, "baseline metric id"): _require_string(
                _require_mapping(metric, "baseline metric").get("unit"), "baseline metric.unit"
            )
            for metric_id, metric in baseline_metrics.items()
        }
    else:
        expected_units = {
            _require_string(_require_mapping(metric, "baseline metric").get("id"), "baseline metric.id"): _require_string(
                _require_mapping(metric, "baseline metric").get("unit"), "baseline metric.unit"
            )
            for metric in _require_list(baseline_metrics, "baseline gate.metrics")
        }
    observed_units: Dict[str, str] = {}
    for index, metric in enumerate(raw_metrics):
        item = _require_mapping(metric, "raw gate.metrics[{0}]".format(index))
        metric_id = _require_string(item.get("id"), "raw gate.metrics[{0}].id".format(index))
        if metric_id in observed_units:
            raise EvidenceError("duplicate raw metric id: {0}".format(metric_id))
        observed_units[metric_id] = _require_string(item.get("unit"), "raw gate.metrics[{0}].unit".format(index))
    if set(observed_units) != set(expected_units):
        raise EvidenceError("metric coverage mismatch")
    for metric_id, unit in observed_units.items():
        if unit != expected_units[metric_id]:
            raise EvidenceError("metric unit mismatch: {0}".format(metric_id))
