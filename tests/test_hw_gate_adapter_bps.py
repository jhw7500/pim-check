from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

import pytest

from config import load_profile
from hw_gate.adapters.base import AdapterContext
from hw_gate.adapters.bps import BPS_SETPOINTS, BpsAdapter, evaluate_bps_gate
from hw_gate.rules import EvidenceError, Verdict
from hw_gate.transaction import (
    StrictHardwareTransaction,
    TransactionRestorationError,
    TransactionState,
)


FIXTURES = Path(__file__).parent / "fixtures" / "hw_gate"
BPS_PATH = ".VHL_CAM.i2c2.ch0.bps"


def _baseline_gate() -> dict:
    baseline = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
    return baseline["gates"]["bps_quick"]


def _actuals() -> Dict[int, int]:
    raw = json.loads((FIXTURES / "bps_raw_pass.json").read_text(encoding="utf-8"))
    return {sample["setpoint_kbps"]: sample["actual_bps"] for sample in raw["samples"]}


class FakeSsh:
    def __init__(self, readback_overrides: Optional[Dict[str, object]] = None) -> None:
        self.current_changes: Dict[str, object] = {}
        self.readback_overrides = readback_overrides or {}
        self.commands = []

    def observed(self, path: str) -> object:
        return self.readback_overrides.get(path, self.current_changes[path])

    def run(self, command: str) -> str:
        self.commands.append(command)
        assert command.startswith("jq -c '")
        path = command.split("'", 2)[1]
        return json.dumps(self.observed(path), separators=(",", ":"))


class FakeSetupManager:
    def __init__(self, ssh: FakeSsh) -> None:
        self.ssh = ssh
        self.checked = []

    def check_current(self, changes: dict) -> bool:
        self.checked.append(copy.deepcopy(changes))
        return all(self.ssh.observed(path) == expected for path, expected in changes.items())


class FakeTransaction:
    def __init__(
        self,
        ssh: FakeSsh,
        calls: list,
        manager: FakeSetupManager,
        run_id: str,
        changes: dict,
        stabilize_sec: int,
        restore_failure: Optional[int],
    ) -> None:
        self.ssh = ssh
        self.calls = calls
        self.manager = manager
        self.run_id = run_id
        self.changes = copy.deepcopy(changes)
        self.stabilize_sec = stabilize_sec
        self.restore_failure = restore_failure
        self._restored_sha: Optional[str] = None

    def __enter__(self) -> "FakeTransaction":
        self.calls.append({
            "manager": self.manager,
            "run_id": self.run_id,
            "changes": copy.deepcopy(self.changes),
            "stabilize_sec": self.stabilize_sec,
        })
        self.ssh.current_changes = copy.deepcopy(self.changes)
        return self

    @property
    def manifest(self) -> dict:
        digit = str(self.changes[BPS_PATH][0] // 1024)
        return {"original_sha256": digit * 64}

    @property
    def original_sha256(self) -> str:
        return self.manifest["original_sha256"]

    @property
    def restored_sha256(self) -> Optional[str]:
        return self._restored_sha

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, exc, traceback
        if self.changes[BPS_PATH][0] == self.restore_failure:
            raise TransactionRestorationError("restore hash mismatch")
        self._restored_sha = self.original_sha256
        return False


class FakeCollector:
    def __init__(self, actuals: Dict[int, object], failure: Optional[str] = None) -> None:
        self.actuals = actuals
        self.failure = failure
        self.configs = []

    def collect(self, ssh: FakeSsh, config: dict) -> dict:
        del ssh
        self.configs.append(copy.deepcopy(config))
        setpoint = config["bps_evidence"]["setpoint_kbps"]
        payload = {
            "boot_id": "boot-{0}".format(setpoint),
            "board_epoch": 1000,
            "setpoint_anchor": 1000,
            "video": "/mnt/sd_cam/final-{0}-ch0.mp4".format(setpoint),
            "mtime": 1001,
            "size_bytes": 200000,
            "duration_sec": 60.0,
            "actual_bps": self.actuals[setpoint],
            "errors": [],
        }
        if self.failure == "stale":
            payload["mtime"] = 999
        elif self.failure == "probe":
            payload["actual_bps"] = None
            payload["errors"] = ["ffprobe did not return exactly one finite positive integer"]
        return payload

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if data["errors"]:
            return False, data["errors"][0]
        if data["mtime"] < data["setpoint_anchor"]:
            return False, "video is not fresh for the setpoint anchor"
        duration_range = config["bps_evidence"]["duration_range_sec"]
        if not duration_range[0] <= data["duration_sec"] <= duration_range[1]:
            return False, "video duration is outside the required range"
        if not isinstance(data["actual_bps"], int) or isinstance(data["actual_bps"], bool):
            return False, "ffprobe bitrate is invalid"
        assert config["bps_evidence"]["channel"] == 0
        return True, "OK"


def _adapter(
    tmp_path: Path,
    *,
    actuals: Optional[Dict[int, object]] = None,
    readback_overrides: Optional[Dict[str, object]] = None,
    collector_failure: Optional[str] = None,
    restore_failure: Optional[int] = None,
    baseline_gate: Optional[dict] = None,
) -> tuple[BpsAdapter, AdapterContext, FakeSetupManager, list]:
    ssh = FakeSsh(readback_overrides)
    manager = FakeSetupManager(ssh)
    calls = []

    def transaction_factory(
        setup_manager: FakeSetupManager,
        run_id: str,
        changes: dict,
        *,
        stabilize_sec: int,
    ) -> FakeTransaction:
        return FakeTransaction(
            ssh, calls, setup_manager, run_id, changes, stabilize_sec, restore_failure,
        )

    collector = FakeCollector(actuals or _actuals(), collector_failure)
    adapter = BpsAdapter(
        setup_manager_factory=lambda injected_ssh: manager,
        transaction_factory=transaction_factory,
        collector=collector,
    )
    context = AdapterContext(
        ssh=ssh,
        baseline_gate=_baseline_gate() if baseline_gate is None else baseline_gate,
        run_id="test-run",
        raw_dir=tmp_path / "raw",
    )
    return adapter, context, manager, calls


def test_collect_raw_is_baseline_independent_and_uses_one_full_transaction_per_setpoint(
    tmp_path: Path,
) -> None:
    adapter, context, manager, calls = _adapter(tmp_path, baseline_gate={})
    fixture = load_profile("profiles", "multi_1ch_0_720p")["setup"]["edgeconf_changes"]

    raw = adapter.collect_raw(context)

    assert [sample["setpoint_kbps"] for sample in raw["samples"]] == list(BPS_SETPOINTS)
    assert [sample["actual_bps"] for sample in raw["samples"]] == [
        _actuals()[setpoint] for setpoint in BPS_SETPOINTS
    ]
    assert len(calls) == len(BPS_SETPOINTS)
    assert len(manager.checked) == len(BPS_SETPOINTS)
    for setpoint, call, checked in zip(BPS_SETPOINTS, calls, manager.checked):
        expected = copy.deepcopy(fixture)
        expected[BPS_PATH] = [setpoint, setpoint]
        assert call["changes"] == expected
        assert checked == expected
        assert call["run_id"] == "test-run-bps-{0}".format(setpoint)
    assert len(raw["preconditions"]) == len(fixture) * len(BPS_SETPOINTS)
    assert all(item["verdict"] == "PASS" for item in raw["preconditions"])
    assert raw["restoration"]["verdict"] == "PASS"
    assert raw["restoration"]["cycles"] == [
        {
            "setpoint_kbps": setpoint,
            "before_sha256": str(setpoint // 1024) * 64,
            "after_sha256": str(setpoint // 1024) * 64,
            "verdict": "PASS",
        }
        for setpoint in BPS_SETPOINTS
    ]


def test_collect_raw_derives_duration_range_from_fixture_recording_time(tmp_path: Path) -> None:
    """The adapter must bind comparable video duration to its controlled fixture."""
    adapter, context, _, _ = _adapter(tmp_path, baseline_gate={})

    adapter.collect_raw(context)

    collector = adapter._collector
    assert isinstance(collector, FakeCollector)
    assert [config["bps_evidence"]["duration_range_sec"] for config in collector.configs] == [
        [55, 65],
        [55, 65],
        [55, 65],
        [55, 65],
    ]


def test_run_normalizes_eight_metrics_and_hashes_the_exact_raw_output(tmp_path: Path) -> None:
    adapter, context, _, _ = _adapter(tmp_path)

    gate = adapter.run(context)

    metric_ids = [metric["id"] for metric in gate["metrics"]]
    assert metric_ids == [
        "bps.ch0.{0}.{1}".format(setpoint, assertion)
        for setpoint in BPS_SETPOINTS
        for assertion in ("target", "baseline")
    ]
    assert len(metric_ids) == len(set(metric_ids)) == 8
    for metric in gate["metrics"]:
        setpoint = int(metric["id"].split(".")[2])
        assert metric["value"] == _actuals()[setpoint]
        assert metric["unit"] == "bps"
        assert metric["verdict"] == "PASS"
    raw_path = context.raw_dir / "bps_quick.json"
    assert gate["raw_output"] == {
        "path": "raw/bps_quick.json",
        "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }
    assert gate["process"] == {"exit_code": 0}
    assert gate["comparability"] == _baseline_gate()["comparability"]
    assert gate["restoration"]["verdict"] == "PASS"
    assert gate["verdict"] == "PASS"
    assert evaluate_bps_gate(gate, _baseline_gate()) is Verdict.PASS


def test_restoration_exception_retains_distinct_before_and_after_hashes(tmp_path: Path) -> None:
    """A real __exit__ mismatch must retain both hashes in the failed raw cycle."""

    class MismatchedRestorationTransaction(StrictHardwareTransaction):
        def _prepare(self) -> None:
            self._original_sha = "1" * 64
            self._record_state(TransactionState.JOURNALED)

        def _apply_and_reboot(self, changes: dict) -> None:
            assert changes[BPS_PATH] == [1024, 1024]
            self._record_state(TransactionState.REBOOTED)

        def _restore_and_verify(self) -> None:
            self._restored_sha = "f" * 64
            raise TransactionRestorationError("restored config SHA256 mismatch")

    ssh = FakeSsh()
    manager = FakeSetupManager(ssh)
    adapter = BpsAdapter(
        setup_manager_factory=lambda injected_ssh: manager,
        transaction_factory=MismatchedRestorationTransaction,
        collector=FakeCollector(_actuals()),
    )
    context = AdapterContext(
        ssh=ssh,
        baseline_gate={},
        run_id="mismatch-run",
        raw_dir=tmp_path / "raw",
    )

    raw = adapter.collect_raw(context)

    assert len(raw["restoration"]["cycles"]) == 1
    cycle = raw["restoration"]["cycles"][0]
    assert cycle["setpoint_kbps"] == 1024
    assert cycle["before_sha256"] == "1" * 64
    assert cycle["after_sha256"] == "f" * 64
    assert cycle["verdict"] == "ERROR"
    assert raw["restoration"]["verdict"] == "ERROR"
    assert raw["errors"] == [{
        "code": "bps.restoration_failed",
        "message": "restored config SHA256 mismatch",
    }]


@pytest.mark.parametrize(
    ("path", "observed"),
    [
        (".VHL_CAM.i2c2.ch0.qp_min", [1, 1]),
        (".VHL_CAM.i2c2.ch0.qp_max", [1, 1]),
        (".VHL_CAM.i2c2.ch0.quant", [0, 0]),
        (".VHL_CAM.i2c2.ch0.profile", [1, 1]),
    ],
)
def test_controlled_encoder_readback_mismatch_rejects_measurement(
    tmp_path: Path, path: str, observed: object,
) -> None:
    adapter, context, _, _ = _adapter(tmp_path, readback_overrides={path: observed})

    gate = adapter.run(context)

    failed = [item for item in gate["preconditions"] if item["verdict"] != "PASS"]
    assert failed
    assert all(item["id"].endswith(path) for item in failed)
    assert gate["metrics"][0]["id"] == "evidence.observed_metric_count"
    assert gate["verdict"] == "ERROR"


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("stale", "not fresh"),
        ("probe", "ffprobe"),
    ],
)
def test_invalid_video_evidence_is_error(
    tmp_path: Path, failure: str, message: str,
) -> None:
    adapter, context, _, _ = _adapter(tmp_path, collector_failure=failure)

    gate = adapter.run(context)

    assert gate["verdict"] == "ERROR"
    assert message in json.dumps(gate["errors"])


def test_missing_baseline_is_error_even_when_collection_succeeds(tmp_path: Path) -> None:
    adapter, context, _, _ = _adapter(tmp_path, baseline_gate={})

    gate = adapter.run(context)

    assert gate["verdict"] == "ERROR"
    assert "baseline" in json.dumps(gate["errors"]).lower()


def test_target_rule_over_ten_percent_fails_without_policy_in_adapter(tmp_path: Path) -> None:
    actuals: Dict[int, object] = _actuals()
    actuals[1024] = 1126401
    baseline = _baseline_gate()
    baseline["metrics"]["bps.ch0.1024.baseline"]["value"] = 1126401
    baseline["metrics"]["bps.ch0.1024.baseline"]["rule"]["reference"] = 1126401
    adapter, context, _, _ = _adapter(tmp_path, actuals=actuals, baseline_gate=baseline)

    gate = adapter.run(context)

    metrics = {metric["id"]: metric for metric in gate["metrics"]}
    assert metrics["bps.ch0.1024.target"]["verdict"] == "FAIL"
    assert metrics["bps.ch0.1024.baseline"]["verdict"] == "PASS"
    assert gate["verdict"] == "FAIL"


def test_baseline_rule_over_five_percent_fails_independently(tmp_path: Path) -> None:
    actuals: Dict[int, object] = _actuals()
    actuals[1024] = 1080000
    adapter, context, _, _ = _adapter(tmp_path, actuals=actuals)

    gate = adapter.run(context)

    metrics = {metric["id"]: metric for metric in gate["metrics"]}
    assert metrics["bps.ch0.1024.target"]["verdict"] == "PASS"
    assert metrics["bps.ch0.1024.baseline"]["verdict"] == "FAIL"
    assert gate["verdict"] == "FAIL"


def test_restore_failure_overrides_would_be_pass(tmp_path: Path) -> None:
    adapter, context, _, _ = _adapter(tmp_path, restore_failure=8192)

    gate = adapter.run(context)

    assert gate["restoration"]["verdict"] == "ERROR"
    assert gate["process"]["exit_code"] != 0
    assert gate["verdict"] == "ERROR"
    assert "restore hash mismatch" in json.dumps(gate["errors"])


def test_legacy_entry_point_rejects_pass_when_restoration_is_not_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import run_bps_quick

    result_path = tmp_path / "bps_quick_results.json"
    monkeypatch.setattr(run_bps_quick, "RESULT", result_path)
    monkeypatch.setattr(
        run_bps_quick,
        "run_local_bps",
        lambda output_path: {
            "verdict": "PASS",
            "restoration": {"verdict": "ERROR"},
            "output_path": str(output_path),
        },
    )

    assert run_bps_quick.main() == 1
    assert json.loads(result_path.read_text(encoding="utf-8"))["verdict"] == "PASS"


@pytest.mark.parametrize(
    ("target_host", "expected_host"),
    [(None, "192.168.214.4"), ("192.168.214.4", "192.168.214.4")],
)
def test_legacy_local_bps_uses_baseline_wired_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_host: Optional[str],
    expected_host: str,
) -> None:
    """Legacy measurement must use the exact host that owns the baseline."""
    import hw_gate.adapters.bps as bps_module

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
        "gates": {"bps_quick": {}},
    }})()
    if target_host is None:
        monkeypatch.delenv("TARGET_HOST", raising=False)
    else:
        monkeypatch.setenv("TARGET_HOST", target_host)
    monkeypatch.setattr(bps_module, "load_baseline", lambda _path: loaded)
    monkeypatch.setattr(
        bps_module,
        "load_profile",
        lambda *_args: {"target": {"host": "192.168.0.5", "user": "root", "password": "root"}},
    )
    monkeypatch.setattr(bps_module, "SshClient", BoundarySsh)
    monkeypatch.setattr(bps_module, "BpsAdapter", StubAdapter)
    monkeypatch.setattr(bps_module, "evaluate_bps_gate", lambda *_args: Verdict.ERROR)

    bps_module.run_local_bps(tmp_path / "result.json")

    assert opened == [expected_host]
    assert closed == [True]


def test_legacy_local_bps_rejects_mismatched_target_identity_before_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching host must not make an uncalibrated module comparable."""
    import hw_gate.adapters.bps as bps_module

    adapter_called: list[bool] = []
    closed: list[bool] = []

    class BoundarySsh:
        def __init__(self, _host: str, **_credentials: object) -> None:
            pass

        def run(self, command: str) -> str:
            if command == "modinfo -n max9296":
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
            adapter_called.append(True)
            return {"verdict": "PASS", "restoration": {"verdict": "PASS"}}

    loaded = type("Loaded", (), {"data": {
        "comparability": {"target_host": "192.168.214.4"},
        "target_identity": [{
            "id": "max9296.module_sha256",
            "kind": "module_sha256",
            "module": "max9296",
            "sha256": "1" * 64,
        }],
        "gates": {"bps_quick": {}},
    }})()
    monkeypatch.delenv("TARGET_HOST", raising=False)
    monkeypatch.setattr(bps_module, "load_baseline", lambda _path: loaded)
    monkeypatch.setattr(
        bps_module,
        "load_profile",
        lambda *_args: {"target": {"user": "root", "password": "root"}},
    )
    monkeypatch.setattr(bps_module, "SshClient", BoundarySsh)
    monkeypatch.setattr(bps_module, "BpsAdapter", StubAdapter)

    with pytest.raises(EvidenceError, match="target identity.*mismatch"):
        bps_module.run_local_bps(tmp_path / "result.json")

    assert adapter_called == []
    assert closed == [True]


def test_legacy_local_bps_rejects_host_outside_baseline_before_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An alternate board must not receive a verdict from wired-target calibration."""
    import hw_gate.adapters.bps as bps_module

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
        "gates": {"bps_quick": {}},
    }})()
    monkeypatch.setenv("TARGET_HOST", "192.168.0.5")
    monkeypatch.setattr(bps_module, "load_baseline", lambda _path: loaded)
    monkeypatch.setattr(
        bps_module,
        "load_profile",
        lambda *_args: {"target": {"user": "root", "password": "root"}},
    )
    monkeypatch.setattr(bps_module, "SshClient", BoundarySsh)
    monkeypatch.setattr(bps_module, "BpsAdapter", StubAdapter)
    monkeypatch.setattr(bps_module, "evaluate_bps_gate", lambda *_args: Verdict.ERROR)

    with pytest.raises(EvidenceError, match="target host.*baseline comparability"):
        bps_module.run_local_bps(tmp_path / "result.json")

    assert opened == []
