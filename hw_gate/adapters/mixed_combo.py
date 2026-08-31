from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from checks.mixed_combo_evidence import MixedComboEvidenceCheck
from config import load_profile
from hw_gate.baseline import load_baseline
from hw_gate.evidence import recompute_overall_verdict
from hw_gate.rules import EvidenceError, Verdict, evaluate_rule
from hw_gate.transaction import (
    StrictHardwareTransaction,
    TransactionRestorationError,
    recover_pending_transaction,
)
from setup import SetupManager
from ssh import SshClient

from .base import AdapterContext, verify_target_identity


_RAW_NAME = "mixed_combo.json"
_FIXTURE_NAME = "mixed_combo_fhd_30fps"
_TARGET_PROFILE_NAME = "multi_1ch_0_720p"
_VIDEO_MODE = {"width": 1920, "height": 1080, "fps": 30}
_COMPARABILITY = {"scenario_matrix": "A-D-1920x1080-30fps"}
_REGISTER_NAMES = ("rotation", "ae", "awb")
_CHANNEL_PATHS = {0: ".VHL_CAM.i2c2.ch0", 1: ".VHL_CAM.i2c2.ch1", 2: ".VHL_CAM.i2c1.ch2", 3: ".VHL_CAM.i2c1.ch3"}
_CHANNEL_BUSES = {0: 2, 1: 2, 2: 1, 3: 1}
_COMBOS = {
    "A": {"vflip": True, "hflip": False, "ae_on": True, "awb": "auto"},
    "B": {"vflip": False, "hflip": True, "ae_on": False, "awb": "off"},
    "C": {"vflip": True, "hflip": True, "ae_on": True, "awb": "off"},
    "D": {"vflip": False, "hflip": False, "ae_on": False, "awb": "auto"},
}
SCENARIOS: Tuple[Dict[str, object], ...] = (
    {"id": 1, "name": "ch1+ch3 cross-bus (internal ch1 slot x 2, single mode)", "enabled": {1: "A", 3: "B"}},
    {"id": 2, "name": "ch0+ch2 cross-bus (internal ch0 slot x 2, single mode)", "enabled": {0: "C", 2: "D"}},
    {"id": 3, "name": "ch0+ch1 same-bus i2c-2 dual", "enabled": {0: "A", 1: "B"}},
    {"id": 4, "name": "quad (all 4 channels, both buses dual)", "enabled": {0: "A", 1: "B", 2: "C", 3: "D"}},
)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _raw_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _scenario_masks(enabled: Dict[int, str]) -> Dict[str, int]:
    counts = {1: 0, 2: 0}
    for channel in enabled:
        counts[_CHANNEL_BUSES[channel]] += 1
    return {str(bus): 3 if counts[bus] == 2 else 0 for bus in (1, 2)}


def _cleanroom_changes(enabled: Dict[int, str]) -> dict:
    changes = {
        ".VHL_CAM.cam_width": _VIDEO_MODE["width"],
        ".VHL_CAM.cam_height": _VIDEO_MODE["height"],
        ".VHL_CAM.fps": _VIDEO_MODE["fps"],
        ".VHL_CAM.recording_time": 1, ".VHL_CAM.muxer": "mp4", ".VHL_CAM.capture.enable": False,
    }
    for channel, path in _CHANNEL_PATHS.items():
        changes.update({
            "{0}.enable".format(path): False, "{0}.vflip".format(path): False,
            "{0}.hflip".format(path): False, "{0}.ae_on".format(path): True,
            "{0}.awb".format(path): "auto", "{0}.bps".format(path): [2048, 1024],
        })
    for channel, combo_name in enabled.items():
        path = _CHANNEL_PATHS[channel]
        combo = _COMBOS[combo_name]
        changes.update({
            "{0}.enable".format(path): True, "{0}.vflip".format(path): combo["vflip"],
            "{0}.hflip".format(path): combo["hflip"], "{0}.ae_on".format(path): combo["ae_on"],
            "{0}.awb".format(path): combo["awb"],
        })
    return changes


class MixedComboAdapter:
    """Run the four mixed-channel scenarios inside one restore-safe campaign."""

    adapter_id = "mixed_combo"
    schema_version = 1

    def __init__(
        self, *,
        profile_loader: Callable[[str, Optional[str]], dict] = load_profile,
        setup_manager_factory: Callable[[SshClient], SetupManager] = SetupManager,
        transaction_factory: Callable[..., StrictHardwareTransaction] = StrictHardwareTransaction,
        collector: Optional[MixedComboEvidenceCheck] = None,
    ) -> None:
        self._profile_loader = profile_loader
        self._setup_manager_factory = setup_manager_factory
        self._transaction_factory = transaction_factory
        self._collector = collector or MixedComboEvidenceCheck()

    @staticmethod
    def _precondition(test_id: int, changes: dict, complete_match: bool) -> dict:
        return {
            "id": "mixed_combo.test{0}.cleanroom_readback".format(test_id),
            "expected": True, "observed": complete_match,
            "verdict": Verdict.PASS.value if complete_match else Verdict.ERROR.value,
            "change_count": len(changes),
        }

    def collect_raw(self, context: AdapterContext) -> dict:
        manager = self._setup_manager_factory(context.ssh)
        scenarios: List[dict] = []
        preconditions: List[dict] = []
        errors: List[dict] = []
        restoration = {"verdict": Verdict.PASS.value}
        try:
            with self._transaction_factory(manager, "{0}-mixed-combo".format(context.run_id), stabilize_sec=30) as transaction:
                for scenario in SCENARIOS:
                    test_id = scenario["id"]
                    enabled = scenario["enabled"]
                    assert isinstance(test_id, int)
                    assert isinstance(enabled, dict)
                    changes = _cleanroom_changes(enabled)
                    transaction.apply_and_reboot(changes)
                    complete_match = bool(manager.check_current(changes))
                    preconditions.append(self._precondition(test_id, changes, complete_match))
                    if not complete_match:
                        errors.append(_error("mixed_combo.readback_mismatch", "scenario {0} complete cleanroom read-back failed".format(test_id)))
                    config = {"mixed_combo_evidence": {
                        "test_id": test_id, "enabled_channels": sorted(enabled),
                        "expected_mode_masks": _scenario_masks(enabled),
                    }}
                    try:
                        evidence = self._collector.collect(context.ssh, config)
                    except Exception as exc:
                        evidence = {"test_id": test_id, "mode_masks": {}, "register_words": {}, "errors": [str(exc)]}
                        errors.append(_error("mixed_combo.collection_failed", "scenario {0}: {1}".format(test_id, exc)))
                    scenarios.append({
                        "test_id": test_id, "name": scenario["name"], "enabled_channels": sorted(enabled),
                        "mode_masks_expected_for_addressing": _scenario_masks(enabled), "evidence": evidence,
                    })
        except TransactionRestorationError as exc:
            restoration = {"verdict": Verdict.ERROR.value}
            errors.append(_error("mixed_combo.restoration_failed", str(exc)))
        except Exception as exc:
            errors.append(_error("mixed_combo.transaction_failed", str(exc)))
        return {
            "schema_version": self.schema_version, "adapter_id": self.adapter_id, "run_id": context.run_id,
            "fixture": _FIXTURE_NAME, "video_mode": copy.deepcopy(_VIDEO_MODE),
            "scenarios": scenarios, "preconditions": preconditions,
            "restoration": restoration, "errors": errors,
        }

    @staticmethod
    def _fallback_metric(observed_count: int) -> dict:
        return {
            "id": "evidence.observed_metric_count", "value": observed_count, "unit": "count",
            "baseline_value": observed_count, "rule": {"kind": "exact", "reference": observed_count},
            "delta": {"absolute": 0, "percent": 0}, "verdict": Verdict.PASS.value,
        }

    @staticmethod
    def _observed_count(raw: dict) -> int:
        count = 0
        for scenario in raw.get("scenarios", []):
            evidence = scenario.get("evidence", {}) if isinstance(scenario, dict) else {}
            masks = evidence.get("mode_masks", {}) if isinstance(evidence, dict) else {}
            words = evidence.get("register_words", {}) if isinstance(evidence, dict) else {}
            if isinstance(masks, dict):
                count += sum(isinstance(value, int) and not isinstance(value, bool) for value in masks.values())
            if isinstance(words, dict):
                for registers in words.values():
                    if isinstance(registers, dict):
                        count += sum(isinstance(value, int) and not isinstance(value, bool) for value in registers.values())
        return count

    @staticmethod
    def _scenario_evidence(raw: dict) -> Dict[int, dict]:
        scenarios = raw.get("scenarios")
        if not isinstance(scenarios, list):
            raise EvidenceError("mixed-combo scenarios are missing")
        expected_ids = [scenario["id"] for scenario in SCENARIOS]
        by_id: Dict[int, dict] = {}
        for scenario in scenarios:
            if not isinstance(scenario, dict) or not isinstance(scenario.get("test_id"), int):
                raise EvidenceError("mixed-combo scenario is malformed")
            test_id = scenario["test_id"]
            if test_id in by_id:
                raise EvidenceError("mixed-combo scenario is duplicated: {0}".format(test_id))
            evidence = scenario.get("evidence")
            if not isinstance(evidence, dict):
                raise EvidenceError("mixed-combo scenario evidence is missing")
            by_id[test_id] = evidence
        if list(sorted(by_id)) != expected_ids:
            raise EvidenceError("mixed-combo scenario coverage is incomplete")
        return by_id

    def _metrics(self, raw: dict, baseline_gate: dict) -> List[dict]:
        if not isinstance(baseline_gate, dict) or baseline_gate.get("adapter_schema_version") != self.schema_version:
            raise EvidenceError("mixed-combo baseline gate is missing or incompatible")
        if baseline_gate.get("comparability") != _COMPARABILITY:
            raise EvidenceError("mixed-combo baseline comparability does not match the FHD 30fps matrix")
        baseline_metrics = baseline_gate.get("metrics")
        if not isinstance(baseline_metrics, dict):
            raise EvidenceError("mixed-combo baseline metrics are missing")
        evidence_by_id = self._scenario_evidence(raw)
        values: Dict[str, int] = {}
        for scenario in SCENARIOS:
            test_id = scenario["id"]
            enabled = scenario["enabled"]
            assert isinstance(test_id, int)
            assert isinstance(enabled, dict)
            evidence = evidence_by_id[test_id]
            if evidence.get("test_id") != test_id:
                raise EvidenceError("mixed-combo scenario evidence test ID mismatch")
            errors = evidence.get("errors")
            if not isinstance(errors, list) or errors:
                raise EvidenceError("mixed-combo scenario {0} collector evidence is invalid".format(test_id))
            masks = evidence.get("mode_masks")
            if not isinstance(masks, dict) or set(masks) != {"1", "2"}:
                raise EvidenceError("mixed-combo scenario {0} mode masks are incomplete".format(test_id))
            for bus in (1, 2):
                value = masks[str(bus)]
                if not isinstance(value, int) or isinstance(value, bool) or value not in (0, 1, 2, 3):
                    raise EvidenceError("mixed-combo scenario {0} mode mask is invalid".format(test_id))
                values["mixed_combo.test{0}.bus{1}.mode_mask".format(test_id, bus)] = value
            registers = evidence.get("register_words")
            expected_channels = {str(channel) for channel in enabled}
            if not isinstance(registers, dict) or set(registers) != expected_channels:
                raise EvidenceError("mixed-combo scenario {0} channel coverage is incomplete".format(test_id))
            for channel in sorted(enabled):
                words = registers[str(channel)]
                if not isinstance(words, dict) or set(words) != set(_REGISTER_NAMES):
                    raise EvidenceError("mixed-combo scenario {0} register coverage is incomplete".format(test_id))
                for name in _REGISTER_NAMES:
                    value = words[name]
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 0xFFFF:
                        raise EvidenceError("mixed-combo scenario {0} register value is invalid".format(test_id))
                    values["mixed_combo.test{0}.ch{1}.{2}".format(test_id, channel, name)] = value
        if set(values) != set(baseline_metrics):
            raise EvidenceError("mixed-combo baseline metric coverage mismatch")
        normalized = []
        for metric_id, baseline_metric in baseline_metrics.items():
            if not isinstance(baseline_metric, dict):
                raise EvidenceError("mixed-combo baseline metric is malformed: {0}".format(metric_id))
            unit = baseline_metric.get("unit")
            normalized.append({"id": metric_id, "value": values[metric_id], "unit": unit, **evaluate_rule(values[metric_id], unit, baseline_metric)})
        return normalized

    def run(self, context: AdapterContext) -> dict:
        raw = self.collect_raw(context)
        context.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = context.raw_dir / _RAW_NAME
        payload = _raw_bytes(raw)
        raw_path.write_bytes(payload)
        errors = copy.deepcopy(raw["errors"])
        try:
            if errors:
                raise EvidenceError("mixed-combo raw collection is incomplete")
            metrics = self._metrics(raw, context.baseline_gate)
        except EvidenceError as exc:
            errors.append(_error("mixed_combo.evaluation_error", str(exc)))
            metrics = [self._fallback_metric(self._observed_count(raw))]
        if errors or raw["restoration"]["verdict"] != Verdict.PASS.value:
            verdict = Verdict.ERROR.value
        elif any(metric["verdict"] == Verdict.FAIL.value for metric in metrics):
            verdict = Verdict.FAIL.value
        else:
            verdict = Verdict.PASS.value
        return {
            "id": self.adapter_id, "adapter_id": self.adapter_id, "adapter_schema_version": self.schema_version,
            "comparability": copy.deepcopy(_COMPARABILITY),
            "process": {"exit_code": 0 if not errors else 1},
            "raw_output": {"path": "raw/{0}".format(_RAW_NAME), "sha256": hashlib.sha256(payload).hexdigest()},
            "preconditions": raw["preconditions"], "metrics": metrics, "restoration": raw["restoration"],
            "diagnostic_refs": ["raw/{0}".format(_RAW_NAME)], "errors": errors, "verdict": verdict,
        }


def evaluate_mixed_combo_gate(gate: dict, baseline_gate: dict) -> Verdict:
    """Run the shared evaluator so only the committed baseline owns exact rules."""
    if (
        gate.get("comparability") != _COMPARABILITY
        or not isinstance(baseline_gate, dict)
        or baseline_gate.get("comparability") != _COMPARABILITY
    ):
        return Verdict.ERROR
    baseline_metrics = baseline_gate.get("metrics", {}) if isinstance(baseline_gate, dict) else {}
    normalized_baseline = {
        "id": "mixed_combo", "adapter_id": "mixed_combo",
        "adapter_schema_version": baseline_gate.get("adapter_schema_version") if isinstance(baseline_gate, dict) else None,
        "metrics": [dict(metric, id=metric_id) for metric_id, metric in baseline_metrics.items()],
    }
    document = {
        "schema_version": 1, "created_at": _utc_now(), "deployment": {"mode": "predeployed", "verified": False},
        "gates": [gate], "verdict": gate.get("verdict", Verdict.ERROR.value),
    }
    return recompute_overall_verdict(document, {"gates": [normalized_baseline]})


def run_local_mixed_combo(output_path: Path) -> dict:
    """Run the adapter for the legacy entry point and centrally recompute its gate."""
    baseline_path = Path(os.environ.get("PIM_HW_BASELINE", "baselines/hw-baseline.json"))
    loaded = load_baseline(baseline_path)
    profile = load_profile("profiles", _TARGET_PROFILE_NAME)
    target = profile.get("target", {})
    baseline_host = loaded.data["comparability"]["target_host"]
    host = os.environ.get("TARGET_HOST", baseline_host)
    if host != baseline_host:
        raise EvidenceError("target host does not match baseline comparability")
    ssh = SshClient(host, user=target.get("user", "root"), password=target.get("password", "root"))
    try:
        recover_pending_transaction(SetupManager(ssh))
        verify_target_identity(ssh, loaded.data)
        gate = MixedComboAdapter().run(AdapterContext(
            ssh=ssh, baseline_gate=loaded.data["gates"]["mixed_combo"],
            run_id="mixed-combo-{0}".format(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")),
            raw_dir=output_path.parent / "raw",
        ))
        gate["verdict"] = evaluate_mixed_combo_gate(gate, loaded.data["gates"]["mixed_combo"]).value
        return gate
    finally:
        ssh.close()
