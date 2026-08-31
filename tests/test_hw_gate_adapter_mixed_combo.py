from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Optional

import pytest

from hw_gate.adapters.base import AdapterContext
from hw_gate.adapters.mixed_combo import MixedComboAdapter, evaluate_mixed_combo_gate
from hw_gate.rules import EvidenceError, Verdict
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
        self.collector_configs: list[dict] = []

    def check_current(self, changes: dict) -> bool:
        self.checked.append(copy.deepcopy(changes))
        return self.current_changes == changes


class FakeTransaction:
    def __init__(
        self, manager: FakeSetupManager, events: list[str], run_id: str,
        restore_failure: bool = False, fail_at: Optional[int] = None,
        fatal_at: Optional[int] = None,
    ) -> None:
        self.manager = manager
        self.events = events
        self.run_id = run_id
        self.restore_failure = restore_failure
        self.fail_at = fail_at
        self.fatal_at = fatal_at
        self.apply_count = 0

    def __enter__(self) -> "FakeTransaction":
        self.events.extend(["snapshot", "journal"])
        return self

    def apply_and_reboot(self, changes: dict) -> None:
        self.apply_count += 1
        self.events.append("apply-reboot-{0}".format(self.apply_count))
        if self.fatal_at == self.apply_count:
            raise SigtermLike("simulated SIGTERM")
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
    def __init__(self, scenarios: list[dict], collector_configs: list[dict]) -> None:
        self._by_id = {item["test_id"]: copy.deepcopy(item["evidence"]) for item in scenarios}
        self._collector_configs = collector_configs

    def collect(self, ssh: FakeSsh, config: dict) -> dict:
        del ssh
        self._collector_configs.append(copy.deepcopy(config))
        return copy.deepcopy(self._by_id[config["mixed_combo_evidence"]["test_id"]])


class SigtermLike(BaseException):
    """Test-only signal-shaped interruption that must not be swallowed as an Exception."""


def _adapter(
    tmp_path: Path, *, scenarios: Optional[list[dict]] = None, restore_failure: bool = False,
    fail_at: Optional[int] = None, fatal_at: Optional[int] = None,
    baseline_gate: Optional[dict] = None,
) -> tuple[MixedComboAdapter, AdapterContext, FakeSetupManager, list[str]]:
    manager = FakeSetupManager()
    events: list[str] = []

    def transaction_factory(
        setup_manager: FakeSetupManager, run_id: str, *, stabilize_sec: int,
    ) -> FakeTransaction:
        assert setup_manager is manager
        assert stabilize_sec == 30
        return FakeTransaction(manager, events, run_id, restore_failure, fail_at, fatal_at)

    adapter = MixedComboAdapter(
        setup_manager_factory=lambda ssh: manager,
        transaction_factory=transaction_factory,
        collector=FixtureCollector(scenarios or _raw_scenarios(), manager.collector_configs),
    )
    context = AdapterContext(
        ssh=FakeSsh(), baseline_gate=baseline_gate or _baseline_gate(),
        run_id="mixed-test", raw_dir=tmp_path / "raw",
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


def test_cleanroom_uses_fhd_30fps_for_every_mixed_scenario(tmp_path: Path) -> None:
    """Catches a 720p or 15fps tuple that exercises a different hardware mode."""
    adapter, context, manager, _ = _adapter(tmp_path)

    raw = adapter.collect_raw(context)

    assert len(manager.checked) == 4
    for changes in manager.checked:
        assert {
            "width": changes[".VHL_CAM.cam_width"],
            "height": changes[".VHL_CAM.cam_height"],
            "fps": changes[".VHL_CAM.fps"],
        } == {"width": 1920, "height": 1080, "fps": 30}
    assert raw["fixture"] == "mixed_combo_fhd_30fps"
    assert raw["video_mode"] == {"width": 1920, "height": 1080, "fps": 30}


def test_legacy_720p_comparability_cannot_authorize_an_fhd_30fps_run(tmp_path: Path) -> None:
    """Catches echoing stale baseline context instead of declaring the actual run context."""
    baseline_gate = _baseline_gate()
    baseline_gate["comparability"] = {"scenario_matrix": "A-D"}
    adapter, context, _, _ = _adapter(tmp_path, baseline_gate=baseline_gate)

    gate = adapter.run(context)

    assert gate["comparability"] == {"scenario_matrix": "A-D-1920x1080-30fps"}
    assert gate["verdict"] == "ERROR"
    assert evaluate_mixed_combo_gate(gate, baseline_gate) is Verdict.ERROR
    assert "comparability" in json.dumps(gate["errors"])


@pytest.mark.parametrize(
    ("scenario_index", "expected_channels", "expected_enabled_channels", "expected_masks"),
    [
        (0, {
            0: {"enable": False, "vflip": False, "hflip": False, "ae_on": True, "awb": "auto"},
            1: {"enable": True, "vflip": True, "hflip": False, "ae_on": True, "awb": "auto"},
            2: {"enable": False, "vflip": False, "hflip": False, "ae_on": True, "awb": "auto"},
            3: {"enable": True, "vflip": False, "hflip": True, "ae_on": False, "awb": "off"},
        }, [1, 3], {"1": 0, "2": 0}),
        (1, {
            0: {"enable": True, "vflip": True, "hflip": True, "ae_on": True, "awb": "off"},
            1: {"enable": False, "vflip": False, "hflip": False, "ae_on": True, "awb": "auto"},
            2: {"enable": True, "vflip": False, "hflip": False, "ae_on": False, "awb": "auto"},
            3: {"enable": False, "vflip": False, "hflip": False, "ae_on": True, "awb": "auto"},
        }, [0, 2], {"1": 0, "2": 0}),
        (2, {
            0: {"enable": True, "vflip": True, "hflip": False, "ae_on": True, "awb": "auto"},
            1: {"enable": True, "vflip": False, "hflip": True, "ae_on": False, "awb": "off"},
            2: {"enable": False, "vflip": False, "hflip": False, "ae_on": True, "awb": "auto"},
            3: {"enable": False, "vflip": False, "hflip": False, "ae_on": True, "awb": "auto"},
        }, [0, 1], {"1": 0, "2": 3}),
        (3, {
            0: {"enable": True, "vflip": True, "hflip": False, "ae_on": True, "awb": "auto"},
            1: {"enable": True, "vflip": False, "hflip": True, "ae_on": False, "awb": "off"},
            2: {"enable": True, "vflip": True, "hflip": True, "ae_on": True, "awb": "off"},
            3: {"enable": True, "vflip": False, "hflip": False, "ae_on": False, "awb": "auto"},
        }, [0, 1, 2, 3], {"1": 3, "2": 3}),
    ],
)
def test_cleanroom_matrix_preserves_literal_abcd_settings_and_collector_masks(
    tmp_path: Path, scenario_index: int, expected_channels: dict,
    expected_enabled_channels: list[int], expected_masks: dict,
) -> None:
    """Catches any changed A/B/C/D assignment, setting value, or collector bus-mask input."""
    adapter, context, manager, _ = _adapter(tmp_path)

    adapter.collect_raw(context)

    observed_channels = {
        channel: {
            key: manager.checked[scenario_index][".VHL_CAM.i2c{0}.ch{1}.{2}".format(
                2 if channel < 2 else 1, channel, key,
            )]
            for key in ("enable", "vflip", "hflip", "ae_on", "awb")
        }
        for channel in range(4)
    }
    assert observed_channels == expected_channels
    assert manager.collector_configs[scenario_index]["mixed_combo_evidence"] == {
        "test_id": scenario_index + 1,
        "enabled_channels": expected_enabled_channels,
        "expected_mode_masks": expected_masks,
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


def test_baseexception_unwinds_the_one_campaign_restoration_without_changing_signal_semantics(
    tmp_path: Path,
) -> None:
    """Catches swallowing signal-like BaseException or skipping its sole restore/reboot/hash attempt."""
    adapter, context, _, events = _adapter(tmp_path, fatal_at=2)

    with pytest.raises(SigtermLike, match="simulated SIGTERM"):
        adapter.collect_raw(context)

    assert events == ["snapshot", "journal", "apply-reboot-1", "apply-reboot-2", "restore-reboot-hash"]


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


@pytest.mark.parametrize(
    ("target_host", "expected_host"),
    [(None, "192.168.214.4"), ("192.168.214.4", "192.168.214.4")],
)
def test_legacy_local_mixed_combo_uses_baseline_wired_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_host: Optional[str],
    expected_host: str,
) -> None:
    """Legacy measurement must use the exact host that owns the baseline."""
    import hw_gate.adapters.mixed_combo as mixed_module

    opened: list[str] = []
    closed: list[bool] = []

    class BoundarySsh:
        def __init__(self, host: str, **_credentials: object) -> None:
            opened.append(host)

        def run(self, command: str) -> str:
            if command == "modinfo -n max9296":
                return "/usr/lib/modules/max9296.ko"
            if command.startswith("readlink -f -- "):
                return "/usr/lib/modules/max9296.ko"
            if command.startswith("sha256sum -- "):
                return "{0}  /usr/lib/modules/max9296.ko".format("1" * 64)
            raise AssertionError("unexpected command: {0}".format(command))

        def close(self) -> None:
            closed.append(True)

    class StubAdapter:
        def run(self, _context: AdapterContext) -> dict:
            return {"verdict": "ERROR", "restoration": {"verdict": "PASS"}}

    loaded = type("Loaded", (), {"data": {
        "comparability": {"target_host": "192.168.214.4"},
        "target_identity": [{
            "id": "max9296.module_sha256",
            "kind": "module_sha256",
            "module": "max9296",
            "sha256": "1" * 64,
        }],
        "gates": {"mixed_combo": {}},
    }})()
    if target_host is None:
        monkeypatch.delenv("TARGET_HOST", raising=False)
    else:
        monkeypatch.setenv("TARGET_HOST", target_host)
    monkeypatch.setattr(mixed_module, "load_baseline", lambda _path: loaded)
    monkeypatch.setattr(
        mixed_module,
        "load_profile",
        lambda *_args: {"target": {"host": "192.168.0.5", "user": "root", "password": "root"}},
    )
    monkeypatch.setattr(mixed_module, "SshClient", BoundarySsh)
    monkeypatch.setattr(mixed_module, "MixedComboAdapter", StubAdapter)
    monkeypatch.setattr(mixed_module, "evaluate_mixed_combo_gate", lambda *_args: Verdict.ERROR)
    monkeypatch.setattr(
        mixed_module, "recover_pending_transaction", lambda _manager: None,
    )

    mixed_module.run_local_mixed_combo(tmp_path / "result.json")

    assert opened == [expected_host]
    assert closed == [True]


def test_legacy_local_mixed_combo_rejects_mismatched_identity_before_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching host must not authorize mixed-combo data from another module."""
    import hw_gate.adapters.mixed_combo as mixed_module

    events: list[str] = []
    closed: list[bool] = []

    class BoundarySsh:
        def __init__(self, _host: str, **_credentials: object) -> None:
            pass

        def run(self, command: str) -> str:
            if command == "modinfo -n max9296":
                events.append("identity")
                return "/usr/lib/modules/max9296.ko"
            if command.startswith("readlink -f -- "):
                return "/usr/lib/modules/max9296.ko"
            if command.startswith("sha256sum -- "):
                return "{0}  /usr/lib/modules/max9296.ko".format("2" * 64)
            raise AssertionError("unexpected command: {0}".format(command))

        def close(self) -> None:
            closed.append(True)

    class StubAdapter:
        def run(self, _context: AdapterContext) -> dict:
            events.append("adapter")
            return {"verdict": "PASS", "restoration": {"verdict": "PASS"}}

    loaded = type("Loaded", (), {"data": {
        "comparability": {"target_host": "192.168.214.4"},
        "target_identity": [{
            "id": "max9296.module_sha256",
            "kind": "module_sha256",
            "module": "max9296",
            "sha256": "1" * 64,
        }],
        "gates": {"mixed_combo": {}},
    }})()
    monkeypatch.delenv("TARGET_HOST", raising=False)
    monkeypatch.setattr(mixed_module, "load_baseline", lambda _path: loaded)
    monkeypatch.setattr(
        mixed_module,
        "load_profile",
        lambda *_args: {"target": {"user": "root", "password": "root"}},
    )
    monkeypatch.setattr(mixed_module, "SshClient", BoundarySsh)
    monkeypatch.setattr(mixed_module, "MixedComboAdapter", StubAdapter)
    monkeypatch.setattr(
        mixed_module,
        "recover_pending_transaction",
        lambda _manager: events.append("recover"),
    )

    with pytest.raises(EvidenceError, match="target identity.*mismatch"):
        mixed_module.run_local_mixed_combo(tmp_path / "result.json")

    assert events == ["recover", "identity"]
    assert closed == [True]


def test_legacy_local_mixed_combo_rejects_host_outside_baseline_before_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An alternate board must not receive a verdict from wired-target calibration."""
    import hw_gate.adapters.mixed_combo as mixed_module

    opened: list[str] = []

    class BoundarySsh:
        def __init__(self, host: str, **_credentials: object) -> None:
            opened.append(host)

        def close(self) -> None:
            pass

    class StubAdapter:
        def run(self, _context: AdapterContext) -> dict:
            return {"verdict": "ERROR", "restoration": {"verdict": "PASS"}}

    loaded = type("Loaded", (), {"data": {
        "comparability": {"target_host": "192.168.214.4"},
        "gates": {"mixed_combo": {}},
    }})()
    monkeypatch.setenv("TARGET_HOST", "192.168.0.5")
    monkeypatch.setattr(mixed_module, "load_baseline", lambda _path: loaded)
    monkeypatch.setattr(
        mixed_module,
        "load_profile",
        lambda *_args: {"target": {"user": "root", "password": "root"}},
    )
    monkeypatch.setattr(mixed_module, "SshClient", BoundarySsh)
    monkeypatch.setattr(mixed_module, "MixedComboAdapter", StubAdapter)
    monkeypatch.setattr(mixed_module, "evaluate_mixed_combo_gate", lambda *_args: Verdict.ERROR)

    with pytest.raises(EvidenceError, match="target host.*baseline comparability"):
        mixed_module.run_local_mixed_combo(tmp_path / "result.json")

    assert opened == []
