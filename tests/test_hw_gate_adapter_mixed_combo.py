from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Optional

import pytest

from hw_gate.adapters.base import AdapterContext
from hw_gate.adapters.mixed_combo import MixedComboAdapter, evaluate_mixed_combo_gate
from hw_gate.rules import Verdict
from hw_gate.transaction import TransactionRestorationError


FIXTURES = Path(__file__).parent / "fixtures" / "hw_gate"


def _baseline_gate() -> dict:
    baseline = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
    return baseline["gates"]["mixed_combo"]


def _raw_scenarios() -> list[dict]:
    return json.loads((FIXTURES / "mixed_combo_raw_pass.json").read_text(encoding="utf-8"))["scenarios"]


class FakeSsh:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSetupManager:
    def __init__(self) -> None:
        self.checked: list[dict] = []
        self.current_changes: dict = {}

    def check_current(self, changes: dict) -> bool:
        self.checked.append(copy.deepcopy(changes))
        return self.current_changes == changes


class FakeTransaction:
    def __init__(
        self, manager: FakeSetupManager, events: list[str], run_id: str,
        restore_failure: bool = False, fail_at: Optional[int] = None,
    ) -> None:
        self.manager = manager
        self.events = events
        self.run_id = run_id
        self.restore_failure = restore_failure
        self.fail_at = fail_at
        self.apply_count = 0

    def __enter__(self) -> "FakeTransaction":
        self.events.extend(["snapshot", "journal"])
        return self

    def apply_and_reboot(self, changes: dict) -> None:
        self.apply_count += 1
        self.events.append("apply-reboot-{0}".format(self.apply_count))
        if self.fail_at == self.apply_count:
            raise RuntimeError("scenario mutation failed")
        self.manager.current_changes = copy.deepcopy(changes)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, exc, traceback
        self.events.append("restore-reboot-hash")
        if self.restore_failure:
            raise TransactionRestorationError("restore hash mismatch")
        return False


class FixtureCollector:
    def __init__(self, scenarios: list[dict]) -> None:
        self._by_id = {item["test_id"]: copy.deepcopy(item["evidence"]) for item in scenarios}

    def collect(self, ssh: FakeSsh, config: dict) -> dict:
        del ssh
        return copy.deepcopy(self._by_id[config["mixed_combo_evidence"]["test_id"]])


def _adapter(
    tmp_path: Path, *, scenarios: Optional[list[dict]] = None, restore_failure: bool = False,
    fail_at: Optional[int] = None,
) -> tuple[MixedComboAdapter, AdapterContext, FakeSetupManager, list[str]]:
    manager = FakeSetupManager()
    events: list[str] = []

    def transaction_factory(
        setup_manager: FakeSetupManager, run_id: str, *, stabilize_sec: int,
    ) -> FakeTransaction:
        assert setup_manager is manager
        assert stabilize_sec == 30
        return FakeTransaction(manager, events, run_id, restore_failure, fail_at)

    adapter = MixedComboAdapter(
        setup_manager_factory=lambda ssh: manager,
        transaction_factory=transaction_factory,
        collector=FixtureCollector(scenarios or _raw_scenarios()),
    )
    context = AdapterContext(
        ssh=FakeSsh(), baseline_gate=_baseline_gate(), run_id="mixed-test", raw_dir=tmp_path / "raw",
    )
    return adapter, context, manager, events


def test_normalization_preserves_the_four_legacy_channel_assignments_as_numeric_metrics(
    tmp_path: Path,
) -> None:
    """Catches an adapter that drops a scenario/channel or keeps legacy hex/boolean summaries."""
    adapter, context, _, _ = _adapter(tmp_path)

    gate = adapter.run(context)

    expected_ids = list(_baseline_gate()["metrics"])
    assert [metric["id"] for metric in gate["metrics"]] == expected_ids
    assert all(isinstance(metric["value"], int) and not isinstance(metric["value"], bool)
               for metric in gate["metrics"])
    assert gate["verdict"] == "PASS"
    assert evaluate_mixed_combo_gate(gate, _baseline_gate()) is Verdict.PASS
    raw_path = context.raw_dir / "mixed_combo.json"
    assert gate["raw_output"] == {
        "path": "raw/mixed_combo.json",
        "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize("mutation", ["scenario", "channel", "mask", "register"])
def test_legacy_success_is_error_when_exact_evidence_is_incomplete(
    tmp_path: Path, mutation: str,
) -> None:
    """Catches trusting legacy all_pass/exit status when a required exact datum is omitted."""
    scenarios = _raw_scenarios()
    if mutation == "scenario":
        scenarios.pop()
    elif mutation == "channel":
        del scenarios[0]["evidence"]["register_words"]["3"]
    elif mutation == "mask":
        del scenarios[1]["evidence"]["mode_masks"]["2"]
    else:
        del scenarios[2]["evidence"]["register_words"]["0"]["awb"]
    adapter, context, _, _ = _adapter(tmp_path, scenarios=scenarios)

    gate = adapter.run(context)

    assert gate["verdict"] == "ERROR"
    assert gate["process"]["exit_code"] == 1
    assert len(gate["metrics"]) == 1
    assert gate["metrics"][0]["id"] == "evidence.observed_metric_count"
    assert isinstance(gate["metrics"][0]["value"], int)
    assert gate["metrics"][0]["verdict"] in {"PASS", "FAIL"}
    assert gate["errors"]


def test_one_campaign_transaction_applies_cleanroom_before_each_readback_and_restores_once(
    tmp_path: Path,
) -> None:
    """Catches per-scenario snapshots, incremental config leakage, or missing final restoration."""
    adapter, context, manager, events = _adapter(tmp_path)

    raw = adapter.collect_raw(context)

    assert events == [
        "snapshot", "journal", "apply-reboot-1", "apply-reboot-2", "apply-reboot-3", "apply-reboot-4",
        "restore-reboot-hash",
    ]
    assert len(manager.checked) == 4
    assert manager.checked[0][".VHL_CAM.i2c2.ch0.enable"] is False
    assert manager.checked[0][".VHL_CAM.i2c2.ch1.enable"] is True
    assert manager.checked[1][".VHL_CAM.i2c2.ch1.enable"] is False
    assert manager.checked[1][".VHL_CAM.i2c1.ch3.enable"] is False
    assert manager.checked[2][".VHL_CAM.i2c2.ch0.enable"] is True
    assert manager.checked[2][".VHL_CAM.i2c1.ch2.enable"] is False
    assert manager.checked[3][".VHL_CAM.i2c1.ch3.enable"] is True
    assert raw["restoration"]["verdict"] == "PASS"


def test_restoration_error_overrides_would_be_pass_and_runs_after_an_early_failure(
    tmp_path: Path,
) -> None:
    """Catches a campaign that reports pass or skips restore/hash verification after a mutation error."""
    adapter, context, _, events = _adapter(tmp_path, restore_failure=True, fail_at=2)

    gate = adapter.run(context)

    assert events == ["snapshot", "journal", "apply-reboot-1", "apply-reboot-2", "restore-reboot-hash"]
    assert gate["restoration"]["verdict"] == "ERROR"
    assert gate["verdict"] == "ERROR"
    assert "restore hash mismatch" in json.dumps(gate["errors"])


def test_legacy_entry_point_requires_central_pass_and_verified_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches the compatibility shim returning success from a legacy summary alone."""
    import run_mixed_combo_verify

    result_path = tmp_path / "mixed_combo_results.json"
    monkeypatch.setattr(run_mixed_combo_verify, "RESULT", result_path)
    monkeypatch.setattr(
        run_mixed_combo_verify,
        "run_local_mixed_combo",
        lambda output_path: {"verdict": "PASS", "restoration": {"verdict": "ERROR"}},
    )

    assert run_mixed_combo_verify.main() == 1
    assert json.loads(result_path.read_text(encoding="utf-8"))["verdict"] == "PASS"
