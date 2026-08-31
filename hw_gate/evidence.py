from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from .rules import EvidenceError, Verdict, _finite_number, evaluate_rule


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RELATIVE_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9][A-Za-z0-9._/-]*$")
_RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_COMPONENT_VERDICTS = {Verdict.PASS.value, Verdict.FAIL.value, Verdict.ERROR.value}


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


def _validate_timestamp(value: object, field: str) -> None:
    timestamp = _require_string(value, field)
    if not _RFC3339_UTC_RE.fullmatch(timestamp):
        raise EvidenceError("{0} must be an RFC3339 UTC timestamp".format(field))
    try:
        dt.datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError("{0} is not a valid timestamp".format(field)) from exc


def _validate_verdict(value: object, field: str, allowed: Set[str]) -> str:
    verdict = _require_string(value, field)
    if verdict not in allowed:
        raise EvidenceError("{0} is not an allowed verdict".format(field))
    return verdict


def _validate_unique_ids(items: Iterable[object], field: str) -> None:
    seen: Set[str] = set()
    for index, item in enumerate(items):
        identifier = _require_string(_require_mapping(item, "{0}[{1}]".format(field, index)).get("id"), "{0}[{1}].id".format(field, index))
        if identifier in seen:
            raise EvidenceError("duplicate {0} id: {1}".format(field, identifier))
        seen.add(identifier)


def _is_safe_precondition_value(value: object) -> bool:
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            _finite_number(value, "precondition value")
        except EvidenceError:
            return False
        return True
    return isinstance(value, list) and all(_is_safe_precondition_value(item) for item in value)


def _precondition_matches(expected: object, observed: object) -> bool:
    if type(expected) is not type(observed):
        return False
    if isinstance(expected, list):
        return len(expected) == len(observed) and all(
            _precondition_matches(expected_item, observed_item)
            for expected_item, observed_item in zip(expected, observed)
        )
    return expected == observed


def _validate_rule(metric: Dict[str, Any], field: str) -> None:
    value = _finite_number(metric.get("value"), "{0}.value".format(field))
    unit = _require_string(metric.get("unit"), "{0}.unit".format(field))
    baseline_value = _finite_number(metric.get("baseline_value"), "{0}.baseline_value".format(field))
    rule = _require_mapping(metric.get("rule"), "{0}.rule".format(field))
    result = evaluate_rule(value, unit, {"value": baseline_value, "unit": unit, "rule": rule})
    delta = _require_mapping(metric.get("delta"), "{0}.delta".format(field))
    _finite_number(delta.get("absolute"), "{0}.delta.absolute".format(field))
    _finite_number(delta.get("percent"), "{0}.delta.percent".format(field))
    _validate_verdict(metric.get("verdict"), "{0}.verdict".format(field), {Verdict.PASS.value, Verdict.FAIL.value})
    # Exercising the rule here rejects malformed configurations even when a
    # producer supplied a plausible-looking verdict. Its result is deliberately
    # not compared; recomputation owns the final verdict.
    del result


def _validate_gate(gate: object, field: str) -> None:
    payload = _require_mapping(gate, field)
    _require_string(payload.get("id"), "{0}.id".format(field))
    _require_string(payload.get("adapter_id"), "{0}.adapter_id".format(field))
    _require_integer(payload.get("adapter_schema_version"), "{0}.adapter_schema_version".format(field))
    process = _require_mapping(payload.get("process"), "{0}.process".format(field))
    _require_integer(process.get("exit_code"), "{0}.process.exit_code".format(field))
    raw_output = _require_mapping(payload.get("raw_output"), "{0}.raw_output".format(field))
    path = _require_string(raw_output.get("path"), "{0}.raw_output.path".format(field))
    if not _SAFE_RELATIVE_PATH_RE.fullmatch(path):
        raise EvidenceError("{0}.raw_output.path must be a safe relative path".format(field))
    digest = _require_string(raw_output.get("sha256"), "{0}.raw_output.sha256".format(field))
    if not _SHA256_RE.fullmatch(digest):
        raise EvidenceError("{0}.raw_output.sha256 must be a lowercase SHA-256".format(field))

    if "identity" in payload:
        _validate_verdict(
            _require_mapping(payload["identity"], "{0}.identity".format(field)).get("verdict"),
            "{0}.identity.verdict".format(field),
            _COMPONENT_VERDICTS,
        )
    restoration = _require_mapping(payload.get("restoration"), "{0}.restoration".format(field))
    _validate_verdict(
        restoration.get("verdict"),
        "{0}.restoration.verdict".format(field),
        _COMPONENT_VERDICTS,
    )

    preconditions = _require_list(payload.get("preconditions"), "{0}.preconditions".format(field))
    _validate_unique_ids(preconditions, "{0}.preconditions".format(field))
    for index, precondition in enumerate(preconditions):
        item = _require_mapping(precondition, "{0}.preconditions[{1}]".format(field, index))
        if "expected" not in item or "observed" not in item:
            raise EvidenceError("{0}.preconditions[{1}] requires expected and observed".format(field, index))
        if not _is_safe_precondition_value(item["expected"]) or not _is_safe_precondition_value(item["observed"]):
            raise EvidenceError("{0}.preconditions[{1}] has an unsafe comparison shape".format(field, index))
        _validate_verdict(item.get("verdict"), "{0}.preconditions[{1}].verdict".format(field, index), _COMPONENT_VERDICTS)

    metrics = _require_list(payload.get("metrics"), "{0}.metrics".format(field))
    if not metrics:
        raise EvidenceError("{0} must contain at least one metric".format(field))
    _validate_unique_ids(metrics, "{0}.metrics".format(field))
    for index, metric in enumerate(metrics):
        _validate_rule(_require_mapping(metric, "{0}.metrics[{1}]".format(field, index)), "{0}.metrics[{1}]".format(field, index))

    _require_list(payload.get("diagnostic_refs"), "{0}.diagnostic_refs".format(field))
    errors = _require_list(payload.get("errors"), "{0}.errors".format(field))
    for index, error in enumerate(errors):
        item = _require_mapping(error, "{0}.errors[{1}]".format(field, index))
        _require_string(item.get("code"), "{0}.errors[{1}].code".format(field, index))
        _require_string(item.get("message"), "{0}.errors[{1}].message".format(field, index))
    _validate_verdict(payload.get("verdict"), "{0}.verdict".format(field), _COMPONENT_VERDICTS)


def validate_structure(document: Dict[str, Any]) -> None:
    """Raise :class:`EvidenceError` when a schema-v1 document is unsafe."""
    payload = _require_mapping(document, "document")
    if _require_integer(payload.get("schema_version"), "schema_version") != 1:
        raise EvidenceError("schema_version must be 1")
    _validate_timestamp(payload.get("created_at"), "created_at")
    deployment = _require_mapping(payload.get("deployment"), "deployment")
    if deployment.get("mode") != "predeployed" or deployment.get("verified") is not False:
        raise EvidenceError("deployment must remain predeployed with verified=false")
    gates = _require_list(payload.get("gates"), "gates")
    verdict = _validate_verdict(payload.get("verdict"), "verdict", {item.value for item in Verdict})
    if not gates:
        board = _require_mapping(payload.get("board"), "board")
        if verdict != Verdict.BUSY.value or board.get("lease_exit_code") != 4:
            raise EvidenceError("BUSY is the only valid zero-gate terminal state")
        return
    if verdict == Verdict.BUSY.value:
        raise EvidenceError("BUSY is finalizer-only and requires zero gates")
    _validate_unique_ids(gates, "gates")
    for index, gate in enumerate(gates):
        _validate_gate(gate, "gates[{0}]".format(index))


def _baseline_gates(baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(baseline, dict):
        raise EvidenceError("baseline must be a mapping")
    raw_gates = baseline.get("gates")
    if raw_gates is None:
        raw_gates = [baseline]
    if not isinstance(raw_gates, list) or not raw_gates:
        raise EvidenceError("baseline must contain gates")
    gates = [_require_mapping(gate, "baseline gate") for gate in raw_gates]
    gate_ids = [_require_string(gate.get("id"), "baseline gate.id") for gate in gates]
    if len(gate_ids) != len(set(gate_ids)):
        raise EvidenceError("baseline contains duplicate gate ids")
    return gates


def _gate_verdict(gate: Dict[str, Any], baseline_gate: Dict[str, Any], canonical_baseline: bool = False) -> Verdict:
    if canonical_baseline:
        from .baseline import assert_gate_coverage

        assert_gate_coverage(gate, baseline_gate)
    if gate.get("adapter_schema_version") != baseline_gate.get("adapter_schema_version"):
        return Verdict.ERROR
    if gate.get("adapter_id") != baseline_gate.get("adapter_id"):
        return Verdict.ERROR
    baseline_metrics = _require_list(baseline_gate.get("metrics"), "baseline.metrics")
    metric_map: Dict[str, Dict[str, Any]] = {}
    for metric in baseline_metrics:
        item = _require_mapping(metric, "baseline metric")
        identifier = _require_string(item.get("id"), "baseline metric.id")
        if identifier in metric_map:
            return Verdict.ERROR
        metric_map[identifier] = item
    current_metrics = _require_list(gate.get("metrics"), "gate.metrics")
    current_ids = {_require_string(_require_mapping(metric, "metric").get("id"), "metric.id") for metric in current_metrics}
    if current_ids != set(metric_map):
        return Verdict.ERROR
    if gate["process"]["exit_code"] != 0 or gate.get("errors"):
        return Verdict.ERROR

    has_fail = False
    for metric in current_metrics:
        item = _require_mapping(metric, "metric")
        baseline_metric = metric_map[item["id"]]
        if item.get("unit") != baseline_metric.get("unit"):
            return Verdict.ERROR
        expected_baseline = _finite_number(baseline_metric.get("value"), "baseline metric.value")
        if item.get("baseline_value") != expected_baseline:
            return Verdict.ERROR
        baseline_rule = _require_mapping(baseline_metric.get("rule"), "baseline metric.rule")
        result = evaluate_rule(
            _finite_number(item.get("value"), "metric.value"),
            _require_string(item.get("unit"), "metric.unit"),
            {"value": expected_baseline, "unit": baseline_metric.get("unit"), "rule": baseline_rule},
        )
        if result["verdict"] == Verdict.FAIL.value:
            has_fail = True
    for component in ("identity", "restoration"):
        if component in gate:
            component_verdict = gate[component].get("verdict")
            if component_verdict == Verdict.ERROR.value:
                return Verdict.ERROR
            if component_verdict != Verdict.PASS.value:
                has_fail = True
    for precondition in gate.get("preconditions", []):
        if not _precondition_matches(precondition.get("expected"), precondition.get("observed")):
            has_fail = True
    return Verdict.FAIL if has_fail else Verdict.PASS


def _canonical_baseline_gates(baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert strict map-keyed baseline gates to the evaluator's gate shape."""
    gates: List[Dict[str, Any]] = []
    for gate_id, gate in baseline["gates"].items():
        metrics = []
        for metric_id, metric in gate["metrics"].items():
            item = dict(metric)
            item["id"] = metric_id
            metrics.append(item)
        gates.append(
            {
                "id": gate_id,
                "adapter_id": gate_id,
                "adapter_schema_version": gate["adapter_schema_version"],
                "comparability": gate["comparability"],
                "metrics": metrics,
            }
        )
    return gates


def _canonical_identity_matches(document: Dict[str, Any], baseline: Dict[str, Any]) -> bool:
    """Revalidate recorded target claims against the committed identity inventory."""
    from checks.target_identity import TargetIdentityCheck

    board = _require_mapping(document.get("board"), "board")
    comparability = baseline["comparability"]
    if board.get("id") != comparability["board_id"]:
        return False
    if board.get("target_host") != comparability["target_host"]:
        return False
    claims = _require_list(board.get("identity"), "board.identity")
    valid, _reason = TargetIdentityCheck().validate(
        {"claims": claims, "errors": []},
        {"target_identity": baseline["target_identity"]},
    )
    return valid


def recompute_overall_verdict(document: Dict[str, Any], baseline: Optional[Dict[str, Any]]) -> Verdict:
    """Recompute a verdict from validated evidence, never trusting producer claims."""
    try:
        validate_structure(document)
        if not document["gates"]:
            return Verdict.BUSY
        if baseline is None:
            return Verdict.ERROR
        baseline_data = getattr(baseline, "data", baseline)
        canonical_baseline = isinstance(baseline_data, dict) and any(
            key in baseline_data for key in ("schema_version", "baseline_version", "target_identity", "calibration")
        )
        if canonical_baseline:
            from .baseline import validate_baseline

            validate_baseline(baseline_data, production=True)
            if document.get("comparability") != baseline_data["comparability"]:
                return Verdict.ERROR
            if not _canonical_identity_matches(document, baseline_data):
                return Verdict.ERROR
            baseline_gates = _canonical_baseline_gates(baseline_data)
        else:
            baseline_gates = _baseline_gates(baseline_data)
        baseline_by_id = {gate["id"]: gate for gate in baseline_gates}
        evidence_gate_ids = {gate["id"] for gate in document["gates"]}
        if evidence_gate_ids != set(baseline_by_id):
            return Verdict.ERROR
        verdicts: List[Verdict] = []
        for gate in document["gates"]:
            baseline_gate = baseline_by_id.get(gate["id"])
            if baseline_gate is None:
                return Verdict.ERROR
            verdicts.append(_gate_verdict(gate, baseline_gate, canonical_baseline=canonical_baseline))
    except (EvidenceError, KeyError, TypeError):
        return Verdict.ERROR
    if Verdict.ERROR in verdicts:
        return Verdict.ERROR
    if Verdict.FAIL in verdicts:
        return Verdict.FAIL
    return Verdict.PASS
