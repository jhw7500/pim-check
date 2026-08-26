from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hw_gate.evidence import EvidenceError, recompute_overall_verdict, validate_structure
from hw_gate.rules import Verdict


FIXTURE = Path(__file__).parent / "fixtures" / "hw_gate" / "evidence_pass.json"


def load_document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def baseline_for(document: dict) -> dict:
    gate = document["gates"][0]
    metric = gate["metrics"][0]
    return {
        "id": gate["id"],
        "adapter_id": gate["adapter_id"],
        "adapter_schema_version": gate["adapter_schema_version"],
        "metrics": [
            {
                "id": metric["id"],
                "value": metric["baseline_value"],
                "unit": metric["unit"],
                "rule": copy.deepcopy(metric["rule"]),
            }
        ],
    }


def test_valid_pass_document_validates_and_recomputes_pass() -> None:
    """Dropping a mandatory schema-v1 field or passing producer output through must fail."""
    document = load_document()

    validate_structure(document)

    assert recompute_overall_verdict(document, baseline_for(document)) is Verdict.PASS


def test_deployment_must_be_predeployed_and_unverified() -> None:
    """Claiming deployment in this measurement-only phase must fail."""
    document = load_document()
    document["deployment"]["verified"] = True

    with pytest.raises(EvidenceError, match="deployment"):
        validate_structure(document)


@pytest.mark.parametrize("mutation", ["duplicate_gate", "duplicate_metric"])
def test_duplicate_stable_ids_are_rejected(mutation: str) -> None:
    """Allowing ambiguous gate or metric identity must fail."""
    document = load_document()
    if mutation == "duplicate_gate":
        document["gates"].append(copy.deepcopy(document["gates"][0]))
    else:
        document["gates"][0]["metrics"].append(copy.deepcopy(document["gates"][0]["metrics"][0]))

    with pytest.raises(EvidenceError, match="duplicate"):
        validate_structure(document)


def test_producer_pass_with_zero_metrics_recomputes_error() -> None:
    """Treating a zero-metric PASS as evidence must fail."""
    document = load_document()
    document["gates"][0]["metrics"] = []

    with pytest.raises(EvidenceError, match="metric"):
        validate_structure(document)


@pytest.mark.parametrize("mutation", ["missing", "new"])
def test_baseline_coverage_missing_or_new_metric_recomputes_error(mutation: str) -> None:
    """Ignoring unbaselined or absent committed measurements must fail."""
    document = load_document()
    baseline = baseline_for(document)
    if mutation == "missing":
        baseline["metrics"].append({"id": "bps.ch1.1024.baseline", "value": 1024, "unit": "bps"})
    else:
        document["gates"][0]["metrics"][0]["id"] = "bps.ch1.1024.baseline"

    assert recompute_overall_verdict(document, baseline) is Verdict.ERROR


def test_adapter_schema_mismatch_recomputes_error() -> None:
    """Accepting a baseline from a different adapter schema must fail."""
    document = load_document()
    baseline = baseline_for(document)
    baseline["adapter_schema_version"] = 2

    assert recompute_overall_verdict(document, baseline) is Verdict.ERROR


def test_evidence_cannot_loosen_immutable_baseline_rule() -> None:
    """Using producer thresholds instead of the baseline's 5% rule must fail."""
    document = load_document()
    baseline = baseline_for(document)
    document["gates"][0]["metrics"][0]["value"] = 1126400
    document["gates"][0]["metrics"][0]["rule"]["max_percent_delta"] = 100.0

    assert recompute_overall_verdict(document, baseline) is Verdict.FAIL


def test_baseline_gate_absent_from_evidence_recomputes_error() -> None:
    """Ignoring a committed baseline gate with no evidence must fail."""
    document = load_document()
    baseline = {"gates": [baseline_for(document), copy.deepcopy(baseline_for(document))]}
    baseline["gates"][1]["id"] = "second_gate"
    baseline["gates"][1]["adapter_id"] = "second_adapter"

    assert recompute_overall_verdict(document, baseline) is Verdict.ERROR


@pytest.mark.parametrize(
    "path, value, message",
    [
        (("created_at",), "2026-08-26", "timestamp"),
        (("gates", 0, "raw_output", "sha256"), "not-a-sha", "sha256"),
        (("gates", 0, "raw_output", "path"), "../raw.json", "path"),
    ],
)
def test_invalid_timestamp_or_raw_reference_is_rejected(path: tuple[object, ...], value: str, message: str) -> None:
    """Accepting unsafe timestamps or artifact references must fail."""
    document = load_document()
    target: object = document
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(EvidenceError, match=message):
        validate_structure(document)


@pytest.mark.parametrize("field", ["preconditions", "restoration", "identity"])
def test_pass_claim_with_failed_gate_component_recomputes_fail(field: str) -> None:
    """Producer PASS must not mask failed preconditions, restoration, or identity."""
    document = load_document()
    gate = document["gates"][0]
    if field == "preconditions":
        gate["preconditions"][0]["observed"] = [1, 0]
    elif field == "restoration":
        gate["restoration"]["verdict"] = "FAIL"
    else:
        gate["identity"]["verdict"] = "FAIL"

    assert recompute_overall_verdict(document, baseline_for(document)) is Verdict.FAIL


def test_precondition_observation_mismatch_recomputes_fail_despite_producer_pass() -> None:
    """Trusting a producer PASS after its observed precondition changed must fail."""
    document = load_document()
    document["gates"][0]["preconditions"][0]["observed"] = [1, 0]

    assert recompute_overall_verdict(document, baseline_for(document)) is Verdict.FAIL


def test_unsupported_precondition_shape_recomputes_error() -> None:
    """Comparing ambiguous nested mappings as preconditions must fail closed."""
    document = load_document()
    document["gates"][0]["preconditions"][0]["observed"] = {"value": 0}

    assert recompute_overall_verdict(document, baseline_for(document)) is Verdict.ERROR


def test_error_precedes_fail() -> None:
    """Giving a metric FAIL precedence over an evidence ERROR must fail."""
    document = load_document()
    gate = document["gates"][0]
    gate["metrics"][0]["verdict"] = "FAIL"
    gate["errors"].append({"code": "adapter.timeout", "message": "timeout"})

    assert recompute_overall_verdict(document, baseline_for(document)) is Verdict.ERROR


def test_busy_is_the_only_zero_gate_terminal_state() -> None:
    """Allowing ordinary zero-gate terminal documents must fail."""
    document = {
        "schema_version": 1,
        "created_at": "2026-08-26T00:00:00Z",
        "deployment": {"mode": "predeployed", "verified": False},
        "board": {"lease_exit_code": 4},
        "gates": [],
        "verdict": "BUSY",
    }

    validate_structure(document)
    assert recompute_overall_verdict(document, None) is Verdict.BUSY

    document["verdict"] = "PASS"
    with pytest.raises(EvidenceError, match="BUSY"):
        validate_structure(document)
