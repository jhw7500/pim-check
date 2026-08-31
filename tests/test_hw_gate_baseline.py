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
from hw_gate.cli import main as hw_gate_main
from hw_gate.evidence import EvidenceError, recompute_overall_verdict, validate_structure
from hw_gate.rules import Verdict


FIXTURE = Path(__file__).parent / "fixtures" / "hw_gate" / "baseline.json"
PRODUCTION_BASELINE = Path(__file__).parents[1] / "baselines" / "hw-baseline.json"
REVIEWED_PASS_FIXTURE = Path(__file__).parent / "fixtures" / "hw_gate" / "evidence_pass.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def raw_gate_from(baseline: dict, gate_id: str = "bps_quick") -> dict:
    gate = baseline["gates"][gate_id]
    if gate_id == "bps_quick":
        restoration = {
            "cycles": [
                {
                    "setpoint_kbps": setpoint,
                    "before_sha256": "a" * 64,
                    "after_sha256": "a" * 64,
                    "verdict": "PASS",
                }
                for setpoint in (1024, 2048, 4096, 8192)
            ],
            "verdict": "PASS",
        }
    else:
        restoration = {
            "before_sha256": "a" * 64,
            "after_sha256": "a" * 64,
            "verdict": "PASS",
        }
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
        "restoration": restoration,
        "diagnostic_refs": [],
        "errors": [],
        "verdict": "PASS",
    }


def raw_document_from(baseline: dict) -> dict:
    identities = []
    for descriptor in baseline["target_identity"]:
        expected_key = "version" if descriptor["kind"] == "module_version" else "sha256"
        claim = {
            "id": descriptor["id"],
            "kind": descriptor["kind"],
            "expected": descriptor[expected_key],
            "actual": descriptor[expected_key],
        }
        if descriptor["kind"].startswith("module_"):
            claim["module"] = descriptor["module"]
        if descriptor["kind"] == "module_sha256":
            claim["path"] = "/usr/lib/modules/{0}.ko".format(descriptor["module"])
        elif descriptor["kind"] == "file_sha256":
            claim["requested_path"] = descriptor["path"]
            claim["path"] = descriptor["path"]
        identities.append(claim)
    return {
        "schema_version": 1,
        "created_at": "2026-08-26T00:00:00Z",
        "deployment": {"mode": "predeployed", "verified": False},
        "board": {
            "id": baseline["comparability"]["board_id"],
            "target_host": baseline["comparability"]["target_host"],
            "identity": identities,
        },
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


def test_committed_production_baseline_recomputes_the_reviewed_evidence_as_pass() -> None:
    """A changed production-fixture binding must not authorize its reviewed PASS claim."""
    loaded = load_baseline(PRODUCTION_BASELINE)
    document = json.loads(REVIEWED_PASS_FIXTURE.read_text(encoding="utf-8"))

    validate_structure(document)
    assert recompute_overall_verdict(document, loaded.data) is Verdict.PASS
    assert hw_gate_main([
        "validate",
        "--evidence", str(REVIEWED_PASS_FIXTURE),
        "--baseline", str(PRODUCTION_BASELINE),
    ]) == 0


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


def test_template_and_synthetic_baseline_bind_the_fixed_wired_target() -> None:
    """Leaving either source contract on legacy WiFi would make wired runs incomparable."""
    template = json.loads(
        (Path(__file__).parents[1] / "baselines" / "hw-baseline.template.json").read_text(encoding="utf-8")
    )

    assert template["comparability"]["target_host"] == "192.168.214.4"
    assert load_fixture()["comparability"]["target_host"] == "192.168.214.4"


def test_mixed_combo_baselines_bind_the_fhd_30fps_matrix() -> None:
    """A 720p/15fps baseline must not authorize FHD/30fps mixed-combo evidence."""
    template = json.loads(
        (Path(__file__).parents[1] / "baselines" / "hw-baseline.template.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {"scenario_matrix": "A-D-1920x1080-30fps"}

    assert load_baseline(PRODUCTION_BASELINE).data["gates"]["mixed_combo"]["comparability"] == expected
    assert template["gates"]["mixed_combo"]["comparability"] == expected
    assert load_fixture()["gates"]["mixed_combo"]["comparability"] == expected


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


@pytest.mark.parametrize("mutation", ["empty", "missing", "unknown"])
def test_production_baseline_requires_each_gate_comparability_inventory(mutation: str) -> None:
    """Allowing a gate to erase or rename its measurement context must fail closed."""
    baseline = load_fixture()
    comparability = baseline["gates"]["bps_quick"]["comparability"]
    if mutation == "empty":
        comparability.clear()
    elif mutation == "missing":
        comparability.pop("encoder")
    else:
        comparability["unreviewed_context"] = "enabled"

    with pytest.raises(EvidenceError, match="comparability"):
        validate_baseline(baseline)


def test_gate_coverage_rejects_a_different_comparability_value() -> None:
    """Comparing the same metric inventory under another encoder must fail closed."""
    baseline = load_fixture()
    raw_gate = raw_gate_from(baseline)
    raw_gate["comparability"]["encoder"] = "h264"

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


@pytest.mark.parametrize("mutation", ["empty", "mismatch"])
def test_recompute_rejects_board_identity_not_matching_committed_baseline(mutation: str) -> None:
    """Producer identity verdicts cannot replace publisher-side claim comparison."""
    baseline = json.loads(PRODUCTION_BASELINE.read_text(encoding="utf-8"))
    document = json.loads(REVIEWED_PASS_FIXTURE.read_text(encoding="utf-8"))
    if mutation == "empty":
        document["board"]["identity"] = []
    else:
        document["board"]["identity"][0]["actual"] = "0" * 64

    assert recompute_overall_verdict(document, baseline) is Verdict.ERROR


@pytest.mark.parametrize("mutation", ["missing", "mismatch"])
def test_recompute_rejects_unverified_canonical_restoration_hashes(mutation: str) -> None:
    """A producer restoration PASS cannot replace exact hash evidence."""
    baseline = json.loads(PRODUCTION_BASELINE.read_text(encoding="utf-8"))
    document = json.loads(REVIEWED_PASS_FIXTURE.read_text(encoding="utf-8"))
    restoration = document["gates"][1]["restoration"]
    if mutation == "missing":
        restoration.clear()
        restoration["verdict"] = "PASS"
    else:
        restoration.update({"before_sha256": "1" * 64, "after_sha256": "2" * 64})

    assert recompute_overall_verdict(document, baseline) is Verdict.ERROR


def test_missing_baseline_requires_a_candidate_and_returns_error() -> None:
    """Missing committed policy must not become an implicit baseline PASS."""
    baseline = load_fixture()

    assert recompute_overall_verdict(raw_document_from(baseline), None) is Verdict.ERROR
