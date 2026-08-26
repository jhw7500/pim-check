from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hw_gate.baseline import (
    LoadedBaseline,
    assert_gate_coverage,
    baseline_sha256,
    load_baseline,
    validate_baseline,
)
from hw_gate.evidence import EvidenceError, recompute_overall_verdict
from hw_gate.rules import Verdict


FIXTURE = Path(__file__).parent / "fixtures" / "hw_gate" / "baseline.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def raw_gate_from(baseline: dict, gate_id: str = "bps_quick") -> dict:
    gate = baseline["gates"][gate_id]
    return {
        "id": gate_id,
        "adapter_id": gate_id,
        "adapter_schema_version": gate["adapter_schema_version"],
        "comparability": copy.deepcopy(gate["comparability"]),
        "process": {"exit_code": 0},
        "raw_output": {
            "path": "raw/{0}.json".format(gate_id),
            "sha256": "0" * 64,
        },
        "preconditions": [],
        "metrics": [
            {
                "id": metric_id,
                "value": metric["value"],
                "unit": metric["unit"],
                "baseline_value": metric["value"],
                "rule": copy.deepcopy(metric["rule"]),
                "delta": {"absolute": 0, "percent": 0},
                "verdict": "PASS",
            }
            for metric_id, metric in gate["metrics"].items()
        ],
        "diagnostic_refs": [],
        "errors": [],
        "verdict": "PASS",
    }


def raw_document_from(baseline: dict) -> dict:
    return {
        "schema_version": 1,
        "created_at": "2026-08-26T00:00:00Z",
        "deployment": {"mode": "predeployed", "verified": False},
        "comparability": copy.deepcopy(baseline["comparability"]),
        "gates": [raw_gate_from(baseline, gate_id) for gate_id in baseline["gates"]],
        "verdict": "PASS",
    }


def test_load_baseline_returns_content_addressed_contract() -> None:
    """Returning content from a different file than the recorded digest must fail."""
    loaded = load_baseline(FIXTURE)

    assert isinstance(loaded, LoadedBaseline)
    assert loaded.data == load_fixture()
    assert loaded.sha256 == baseline_sha256(FIXTURE)


def test_baseline_sha256_is_sha256_of_exact_file_bytes(tmp_path: Path) -> None:
    """Hashing decoded JSON instead of exact committed bytes must fail."""
    path = tmp_path / "baseline.json"
    path.write_bytes(b'{"schema_version": 1}\n')

    assert baseline_sha256(path) == "48e4ce397017e1389eff57a56b84e8a6f8d7eb58a94f893acaa49d55e7718176"


@pytest.mark.parametrize(
    "path, value, message",
    [
        (("schema_version",), True, "schema_version"),
        (("baseline_version",), 1, "baseline_version"),
        (("source_commit",), "not-a-commit", "source_commit"),
        (("comparability", "encoder"), True, "comparability"),
    ],
)
def test_baseline_rejects_wrong_schema_or_unsafe_types(path: tuple[str, ...], value: object, message: str) -> None:
    """Accepting bool lookalikes or malformed schema metadata would weaken the contract."""
    baseline = load_fixture()
    target: dict = baseline
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(EvidenceError, match=message):
        validate_baseline(baseline)


def test_production_baseline_requires_a_typed_identity_claim() -> None:
    """A baseline without an asserted target identity must never authorize PASS."""
    baseline = load_fixture()
    baseline["target_identity"] = []

    with pytest.raises(EvidenceError, match="identity"):
        validate_baseline(baseline)


def test_duplicate_identity_ids_are_rejected() -> None:
    """Allowing ambiguous identity claim IDs would make verification non-deterministic."""
    baseline = load_fixture()
    baseline["target_identity"].append(copy.deepcopy(baseline["target_identity"][0]))

    with pytest.raises(EvidenceError, match="duplicate"):
        validate_baseline(baseline)


def test_unknown_policy_keys_are_rejected() -> None:
    """Ignoring a misspelled policy key could silently relax a committed gate."""
    baseline = load_fixture()
    baseline["gates"]["bps_quick"]["metrics"]["bps.ch0.1024.target"]["max_delta_percent"] = 99

    with pytest.raises(EvidenceError, match="unknown"):
        validate_baseline(baseline)


@pytest.mark.parametrize("mutation", ["missing", "new", "wrong_unit"])
def test_production_baseline_requires_the_committed_metric_inventory(mutation: str) -> None:
    """Allowing a changed metric set or unit in committed policy would authorize incompatible evidence."""
    baseline = load_fixture()
    metrics = baseline["gates"]["bps_quick"]["metrics"]
    if mutation == "missing":
        metrics.pop("bps.ch0.1024.target")
    elif mutation == "new":
        metrics["bps.ch0.1024.unreviewed"] = copy.deepcopy(metrics["bps.ch0.1024.target"])
    else:
        metrics["bps.ch0.1024.target"]["unit"] = "kbps"

    with pytest.raises(EvidenceError, match="metric"):
        validate_baseline(baseline)


def test_template_calibration_marker_is_not_a_production_baseline() -> None:
    """Treating an unmeasured calibration placeholder as a production value must fail."""
    template = json.loads(
        (Path(__file__).parents[1] / "baselines" / "hw-baseline.template.json").read_text(encoding="utf-8")
    )

    validate_baseline(template, production=False)
    with pytest.raises(EvidenceError, match="calibration_required"):
        validate_baseline(template)


def test_gate_coverage_requires_exact_metric_set_and_units() -> None:
    """Missing, new, or differently-unit measurements must fail before rule evaluation."""
    baseline = load_fixture()
    raw_gate = raw_gate_from(baseline)
    baseline_gate = baseline["gates"]["bps_quick"]

    assert_gate_coverage(raw_gate, baseline_gate)

    missing = copy.deepcopy(raw_gate)
    missing["metrics"].pop()
    with pytest.raises(EvidenceError, match="coverage"):
        assert_gate_coverage(missing, baseline_gate)

    new = copy.deepcopy(raw_gate)
    new["metrics"][0]["id"] = "bps.ch0.1024.unreviewed"
    with pytest.raises(EvidenceError, match="coverage"):
        assert_gate_coverage(new, baseline_gate)

    wrong_unit = copy.deepcopy(raw_gate)
    wrong_unit["metrics"][0]["unit"] = "kbps"
    with pytest.raises(EvidenceError, match="unit"):
        assert_gate_coverage(wrong_unit, baseline_gate)


def test_gate_coverage_requires_matching_comparability() -> None:
    """Comparing measurements made under a different fixture must fail closed."""
    baseline = load_fixture()
    raw_gate = raw_gate_from(baseline)
    raw_gate["comparability"] = {"scenario": "different"}

    with pytest.raises(EvidenceError, match="comparability"):
        assert_gate_coverage(raw_gate, baseline["gates"]["bps_quick"])


def test_recompute_uses_committed_baseline_coverage_and_rules() -> None:
    """Using artifact rules or incomplete committed coverage must not produce PASS."""
    baseline = load_fixture()
    document = raw_document_from(baseline)

    assert recompute_overall_verdict(document, baseline) is Verdict.PASS

    document["gates"][0]["metrics"][0]["value"] *= 2
    document["gates"][0]["metrics"][0]["rule"] = {"kind": "relative", "max_percent_delta": 100}
    assert recompute_overall_verdict(document, baseline) is Verdict.FAIL

    document = raw_document_from(baseline)
    document["gates"][0]["metrics"].pop()
    assert recompute_overall_verdict(document, baseline) is Verdict.ERROR


def test_missing_baseline_requires_a_candidate_and_returns_error() -> None:
    """Missing committed policy must not become an implicit baseline PASS."""
    baseline = load_fixture()

    assert recompute_overall_verdict(raw_document_from(baseline), None) is Verdict.ERROR
