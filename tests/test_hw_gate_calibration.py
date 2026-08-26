from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Dict, Optional

import pytest

from hw_gate.baseline import validate_baseline
from hw_gate.calibration import build_candidate, write_candidate
from hw_gate.rules import EvidenceError


TEMPLATE_PATH = Path(__file__).parents[1] / "baselines" / "hw-baseline.template.json"
SETPOINTS = (1024, 2048, 4096, 8192)
IDENTITY_SHA = "a" * 64


def _template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _run(run_number: int, actuals: Optional[Dict[int, int]] = None) -> dict:
    values = actuals or {
        1024: 1_024_000 + (run_number - 2) * 10_000,
        2048: 2_048_000 + (run_number - 2) * 20_000,
        4096: 4_096_000 + (run_number - 2) * 40_000,
        8192: 8_192_000 + (run_number - 2) * 80_000,
    }
    return {
        "run_id": "calibration-run-{0}".format(run_number),
        "identity": [{
            "id": "max9296.module_sha256",
            "kind": "module_sha256",
            "module": "max9296",
            "path": "/lib/modules/max9296.ko",
            "actual": IDENTITY_SHA,
        }],
        "raw": {
            "schema_version": 1,
            "adapter_id": "bps_quick",
            "run_id": "calibration-run-{0}".format(run_number),
            "fixture": "multi_1ch_0_720p",
            "samples": [
                {"setpoint_kbps": setpoint, "actual_bps": values[setpoint]}
                for setpoint in SETPOINTS
            ],
            "preconditions": [],
            "restoration": {
                "cycles": [{
                    "setpoint_kbps": setpoint,
                    "before_sha256": "b" * 64,
                    "after_sha256": "b" * 64,
                    "verdict": "PASS",
                } for setpoint in SETPOINTS],
                "verdict": "PASS",
            },
            "errors": [],
        },
    }


def test_three_independent_runs_build_a_reviewable_production_baseline() -> None:
    """Dropping provenance or committing a non-median reference must fail review."""
    runs = [_run(1), _run(2), _run(3)]

    candidate = build_candidate(_template(), runs)

    assert candidate["eligible"] is True
    assert candidate["reasons"] == []
    assert candidate["source_runs"] == runs
    baseline = candidate["baseline"]
    assert baseline["target_identity"] == [{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": IDENTITY_SHA,
    }]
    assert baseline["calibration"]["bps"] == {
        "source_run_ids": ["calibration-run-1", "calibration-run-2", "calibration-run-3"],
        "samples": {
            "1024": [1_014_000, 1_024_000, 1_034_000],
            "2048": [2_028_000, 2_048_000, 2_068_000],
            "4096": [4_056_000, 4_096_000, 4_136_000],
            "8192": [8_112_000, 8_192_000, 8_272_000],
        },
    }
    for setpoint in SETPOINTS:
        metric = baseline["gates"]["bps_quick"]["metrics"][
            "bps.ch0.{0}.baseline".format(setpoint)
        ]
        assert metric == {
            "value": setpoint * 1000,
            "unit": "bps",
            "rule": {
                "kind": "relative",
                "reference": setpoint * 1000,
                "max_percent_delta": 5,
            },
        }
    validate_baseline(baseline)


@pytest.mark.parametrize("run_count", [2, 4])
def test_candidate_requires_exactly_three_runs(run_count: int) -> None:
    """Accepting fewer or extra samples would violate the fixed calibration design."""
    candidate = build_candidate(_template(), [_run(index) for index in range(1, run_count + 1)])

    assert candidate["eligible"] is False
    assert candidate["baseline"] is None
    assert any("exactly three" in reason for reason in candidate["reasons"])


def test_duplicate_run_ids_are_not_independent_samples() -> None:
    """Counting the same run twice would fabricate independent calibration evidence."""
    runs = [_run(1), _run(2), _run(3)]
    runs[2]["run_id"] = runs[1]["run_id"]
    runs[2]["raw"]["run_id"] = runs[1]["run_id"]

    candidate = build_candidate(_template(), runs)

    assert candidate["eligible"] is False
    assert any("independent" in reason for reason in candidate["reasons"])


def test_any_sample_outside_ten_percent_makes_candidate_explicitly_ineligible() -> None:
    """One out-of-target setpoint must fail bootstrap instead of widening policy."""
    runs = [_run(1), _run(2), _run(3)]
    runs[2]["raw"]["samples"][0]["actual_bps"] = 1_126_401

    candidate = build_candidate(_template(), runs)

    assert candidate["eligible"] is False
    assert candidate["baseline"] is None
    analysis = candidate["analysis"]["setpoints"]["1024"]
    assert analysis["eligible"] is False
    assert analysis["samples"] == [1_014_000, 1_024_000, 1_126_401]
    assert any("10%" in reason for reason in analysis["reasons"])


def test_sample_to_median_deviation_over_five_percent_is_ineligible() -> None:
    """Stable target accuracy cannot substitute for the independent 5% spread rule."""
    runs = [_run(1), _run(2), _run(3)]
    values = [923_000, 1_024_000, 1_125_000]
    for run, actual in zip(runs, values):
        run["raw"]["samples"][0]["actual_bps"] = actual

    candidate = build_candidate(_template(), runs)

    analysis = candidate["analysis"]["setpoints"]["1024"]
    assert candidate["eligible"] is False
    assert analysis["median"] == 1_024_000
    assert analysis["max_sample_to_median_percent"] == pytest.approx(9.86328125)
    assert any("5%" in reason for reason in analysis["reasons"])


def test_missing_setpoint_or_failed_restoration_is_ineligible() -> None:
    """A partial or unrestored controlled run must never contribute a baseline."""
    missing = [_run(1), _run(2), _run(3)]
    missing[1]["raw"]["samples"].pop()
    failed_restore = [_run(1), _run(2), _run(3)]
    failed_restore[2]["raw"]["restoration"]["verdict"] = "ERROR"

    missing_candidate = build_candidate(_template(), missing)
    restore_candidate = build_candidate(_template(), failed_restore)

    assert missing_candidate["eligible"] is False
    assert any("8192" in reason for reason in missing_candidate["reasons"])
    assert restore_candidate["eligible"] is False
    assert any("restoration" in reason for reason in restore_candidate["reasons"])


def test_unmatched_restoration_hash_is_ineligible() -> None:
    """A PASS label cannot replace the exact before/after restoration proof."""
    runs = [_run(1), _run(2), _run(3)]
    runs[1]["raw"]["restoration"]["cycles"][2]["after_sha256"] = "d" * 64

    candidate = build_candidate(_template(), runs)

    assert candidate["eligible"] is False
    assert any("restoration hash" in reason for reason in candidate["reasons"])


def test_identity_claims_must_be_populated_and_stable_across_runs() -> None:
    """A median from an unknown or changing target must not become policy."""
    missing = [_run(1), _run(2), _run(3)]
    missing[1]["identity"] = []
    changed = [_run(1), _run(2), _run(3)]
    changed[2]["identity"][0]["actual"] = "c" * 64

    missing_candidate = build_candidate(_template(), missing)
    changed_candidate = build_candidate(_template(), changed)

    assert missing_candidate["eligible"] is False
    assert any("identity" in reason for reason in missing_candidate["reasons"])
    assert changed_candidate["eligible"] is False
    assert any("identity" in reason for reason in changed_candidate["reasons"])


@pytest.mark.parametrize("mutation", ["duplicate", "malformed", "extra"])
def test_candidate_rejects_duplicate_malformed_or_extra_identity_claims(
    mutation: str,
) -> None:
    """Identity coverage must be one exact, well-formed claim per template ID."""
    runs = [_run(1), _run(2), _run(3)]
    claims = runs[1]["identity"]
    if mutation == "duplicate":
        claims.append(copy.deepcopy(claims[0]))
    elif mutation == "malformed":
        claims.append("not-an-identity-object")
    else:
        claims.append({
            "id": "unexpected.module_sha256",
            "kind": "module_sha256",
            "module": "unexpected",
            "actual": "e" * 64,
        })

    candidate = build_candidate(_template(), runs)

    assert candidate["eligible"] is False
    assert candidate["baseline"] is None
    assert any("identity" in reason for reason in candidate["reasons"])


def test_writer_creates_only_private_candidate_and_refuses_production_baseline(
    tmp_path: Path,
) -> None:
    """Calibration output must remain review-only and cannot auto-promote policy."""
    candidate = build_candidate(_template(), [_run(1), _run(2), _run(3)])
    output = tmp_path / "review" / "candidate.json"

    write_candidate(output, candidate)

    assert json.loads(output.read_text(encoding="utf-8")) == candidate
    assert output.stat().st_mode & 0o777 == 0o600
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [output]

    production = Path(__file__).parents[1] / "baselines" / "hw-baseline.json"
    with pytest.raises(EvidenceError, match="production baseline"):
        write_candidate(production, candidate)

    link = tmp_path / "production-link.json"
    try:
        link.symlink_to(production)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(EvidenceError, match="production baseline"):
        write_candidate(link, candidate)


def test_calibrate_cli_requires_three_repetitions_and_uses_raw_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling evaluated adapter.run would make bootstrap depend on its missing median."""
    from hw_gate import cli

    events: list[str] = []

    class FakeSsh:
        def __init__(self, host: str) -> None:
            events.append("ssh:{0}".format(host))

        def close(self) -> None:
            events.append("close")

    class FakeIdentity:
        def collect(self, ssh: object, config: dict) -> dict:
            del ssh, config
            events.append("identity")
            return {"claims": copy.deepcopy(_run(1)["identity"]), "errors": []}

    class FakeAdapter:
        def run(self, context: object) -> dict:
            raise AssertionError("calibration must not call baseline-evaluated run()")

        def collect_raw(self, context: object) -> dict:
            run_number = len([event for event in events if event == "collect"]) + 1
            events.append("collect")
            raw = copy.deepcopy(_run(run_number)["raw"])
            raw["run_id"] = context.run_id
            return raw

    monkeypatch.setattr(cli, "SshClient", FakeSsh)
    monkeypatch.setattr(cli, "SetupManager", lambda ssh: object())
    monkeypatch.setattr(cli, "recover_pending_transaction", lambda manager: events.append("recover"))
    monkeypatch.setattr(cli, "TargetIdentityCheck", FakeIdentity)
    monkeypatch.setattr(cli, "BpsAdapter", FakeAdapter)
    output = tmp_path / "candidate.json"

    assert cli.main([
        "calibrate",
        "--template", str(TEMPLATE_PATH),
        "--target-host", "192.168.0.5",
        "--repetitions", "3",
        "--output", str(output),
    ]) == 0
    assert events == [
        "ssh:192.168.0.5", "recover",
        "identity", "collect", "identity", "collect", "identity", "collect", "close",
    ]
    assert json.loads(output.read_text(encoding="utf-8"))["eligible"] is True

    for invalid in ("2", "4"):
        with pytest.raises(SystemExit) as exc_info:
            cli.main([
                "calibrate",
                "--template", str(TEMPLATE_PATH),
                "--target-host", "192.168.0.5",
                "--repetitions", invalid,
                "--output", str(tmp_path / ("candidate-" + invalid + ".json")),
            ])
        assert exc_info.value.code == 2


def test_calibrate_cli_maps_ssh_close_failure_to_error_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An eligible candidate cannot turn an infrastructure close failure into exit 0."""
    from hw_gate import cli

    collected = []

    class FailingCloseSsh:
        def __init__(self, host: str) -> None:
            assert host == "192.168.0.5"

        def close(self) -> None:
            raise RuntimeError("close transport failed")

    class FakeIdentity:
        def collect(self, ssh: object, config: dict) -> dict:
            del ssh, config
            return {"claims": copy.deepcopy(_run(1)["identity"]), "errors": []}

    class FakeAdapter:
        def collect_raw(self, context: object) -> dict:
            run_number = len(collected) + 1
            collected.append(context.run_id)
            raw = copy.deepcopy(_run(run_number)["raw"])
            raw["run_id"] = context.run_id
            return raw

    monkeypatch.setattr(cli, "SshClient", FailingCloseSsh)
    monkeypatch.setattr(cli, "SetupManager", lambda ssh: object())
    monkeypatch.setattr(cli, "recover_pending_transaction", lambda manager: False)
    monkeypatch.setattr(cli, "TargetIdentityCheck", FakeIdentity)
    monkeypatch.setattr(cli, "BpsAdapter", FakeAdapter)
    output = tmp_path / "candidate.json"

    result = cli.main([
        "calibrate",
        "--template", str(TEMPLATE_PATH),
        "--target-host", "192.168.0.5",
        "--repetitions", "3",
        "--output", str(output),
    ])

    assert result == 2
    assert json.loads(output.read_text(encoding="utf-8"))["eligible"] is True


def test_calibrate_cli_retains_ineligible_candidate_on_identity_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity preflight failure must remain reviewable without reaching mutation."""
    from hw_gate import cli

    class FakeSsh:
        def __init__(self, host: str) -> None:
            assert host == "192.168.0.5"

        def close(self) -> None:
            pass

    class FailedIdentity:
        def collect(self, ssh: object, config: dict) -> dict:
            del ssh, config
            return {"claims": [], "errors": ["modinfo module path not found"]}

    class MutationMustNotRun:
        def collect_raw(self, context: object) -> dict:
            del context
            raise AssertionError("identity failure must stop before BPS mutation")

    monkeypatch.setattr(cli, "SshClient", FakeSsh)
    monkeypatch.setattr(cli, "SetupManager", lambda ssh: object())
    monkeypatch.setattr(cli, "recover_pending_transaction", lambda manager: False)
    monkeypatch.setattr(cli, "TargetIdentityCheck", FailedIdentity)
    monkeypatch.setattr(cli, "BpsAdapter", MutationMustNotRun)
    output = tmp_path / "identity-error-candidate.json"

    result = cli.main([
        "calibrate",
        "--template", str(TEMPLATE_PATH),
        "--target-host", "192.168.0.5",
        "--repetitions", "3",
        "--output", str(output),
    ])

    assert result == 2
    candidate = json.loads(output.read_text(encoding="utf-8"))
    assert candidate["eligible"] is False
    assert candidate["baseline"] is None
    assert len(candidate["source_runs"]) == 1
    assert candidate["source_runs"][0]["identity"] == []
    assert candidate["source_runs"][0]["identity_errors"] == [
        "modinfo module path not found"
    ]
    assert candidate["source_runs"][0]["raw"] is None
