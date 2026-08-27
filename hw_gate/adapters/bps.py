from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable, List, Optional

from checks.bps_evidence import BpsEvidenceCheck
from config import load_profile
from hw_gate.baseline import load_baseline
from hw_gate.evidence import recompute_overall_verdict
from hw_gate.rules import EvidenceError, Verdict, evaluate_rule
from hw_gate.transaction import StrictHardwareTransaction, TransactionRestorationError
from setup import EDGECONF_PATH, SetupManager
from ssh import SshClient

from .base import AdapterContext


BPS_SETPOINTS = (1024, 2048, 4096, 8192)
_BPS_PATH = ".VHL_CAM.i2c2.ch0.bps"
_FIXTURE_NAME = "multi_1ch_0_720p"
_RAW_NAME = "bps_quick.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _raw_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _readback(ssh: SshClient, path: str) -> object:
    output = ssh.run("jq -c '{0}' {1}".format(path, EDGECONF_PATH))
    if not isinstance(output, str):
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message}


class BpsAdapter:
    """Collect controlled BPS samples, then apply only committed baseline rules."""

    adapter_id = "bps_quick"
    schema_version = 1

    def __init__(
        self,
        *,
        profile_loader: Callable[[str, Optional[str]], dict] = load_profile,
        setup_manager_factory: Callable[[SshClient], SetupManager] = SetupManager,
        transaction_factory: Callable[..., StrictHardwareTransaction] = StrictHardwareTransaction,
        collector: Optional[BpsEvidenceCheck] = None,
    ) -> None:
        self._profile_loader = profile_loader
        self._setup_manager_factory = setup_manager_factory
        self._transaction_factory = transaction_factory
        self._collector = collector or BpsEvidenceCheck()

    def _fixture(self) -> tuple[dict, int]:
        profile = self._profile_loader("profiles", _FIXTURE_NAME)
        setup = profile.get("setup")
        if not isinstance(setup, dict):
            raise EvidenceError("BPS fixture setup is missing")
        changes = setup.get("edgeconf_changes")
        if not isinstance(changes, dict) or not changes:
            raise EvidenceError("BPS fixture edgeconf changes are missing")
        stabilize_sec = setup.get("stabilize_sec", 120)
        if isinstance(stabilize_sec, bool) or not isinstance(stabilize_sec, int):
            raise EvidenceError("BPS fixture stabilize_sec is invalid")
        return copy.deepcopy(changes), stabilize_sec

    def collect_raw(self, context: AdapterContext) -> dict:
        """Collect controlled samples without consulting a baseline.

        Calibration calls this exact path before a median exists. Normal runs
        call it first and only then apply the committed target and baseline
        rules, so calibration can never depend on the value it is creating.
        """
        fixture, stabilize_sec = self._fixture()
        manager = self._setup_manager_factory(context.ssh)
        samples: List[dict] = []
        preconditions: List[dict] = []
        cycles: List[dict] = []
        errors: List[dict] = []

        for setpoint in BPS_SETPOINTS:
            changes = copy.deepcopy(fixture)
            changes[_BPS_PATH] = [setpoint, setpoint]
            cycle = {"setpoint_kbps": setpoint, "verdict": Verdict.ERROR.value}
            pending_sample: Optional[dict] = None
            stop_after_cycle = False
            transaction = None
            try:
                transaction = self._transaction_factory(
                    manager,
                    "{0}-bps-{1}".format(context.run_id, setpoint),
                    changes,
                    stabilize_sec=stabilize_sec,
                )
                with transaction:
                    complete_match = manager.check_current(changes)
                    setpoint_preconditions = []
                    for path, expected in changes.items():
                        observed = _readback(context.ssh, path)
                        matches = type(observed) is type(expected) and observed == expected
                        item = {
                            "id": "bps.{0}.readback.{1}".format(setpoint, path),
                            "expected": copy.deepcopy(expected),
                            "observed": observed,
                            "verdict": Verdict.PASS.value if matches else Verdict.ERROR.value,
                        }
                        setpoint_preconditions.append(item)
                        preconditions.append(item)

                    if not complete_match or any(
                        item["verdict"] != Verdict.PASS.value for item in setpoint_preconditions
                    ):
                        errors.append(_error(
                            "bps.readback_mismatch",
                            "setpoint {0} complete controlled fixture read-back failed".format(setpoint),
                        ))
                    else:
                        collector_config = {
                            "bps_evidence": {
                                "channel": 0,
                                "setpoint_kbps": setpoint,
                                "poll_timeout_sec": stabilize_sec,
                            }
                        }
                        measurement = self._collector.collect(context.ssh, collector_config)
                        valid, reason = self._collector.validate(measurement, collector_config)
                        if not valid:
                            errors.append(_error(
                                "bps.measurement_invalid",
                                "setpoint {0}: {1}".format(setpoint, reason),
                            ))
                        else:
                            actual_bps = measurement.get("actual_bps")
                            if (
                                not isinstance(actual_bps, int)
                                or isinstance(actual_bps, bool)
                                or actual_bps <= 0
                            ):
                                errors.append(_error(
                                    "bps.numeric_invalid",
                                    "setpoint {0}: actual_bps is not a positive integer".format(setpoint),
                                ))
                            else:
                                pending_sample = {
                                    "setpoint_kbps": setpoint,
                                    "actual_bps": actual_bps,
                                    "measurement": measurement,
                                }
                original_sha = transaction.original_sha256
                restored_sha = transaction.restored_sha256
                if (
                    not isinstance(original_sha, str)
                    or not _SHA256_RE.fullmatch(original_sha)
                    or not isinstance(restored_sha, str)
                    or not _SHA256_RE.fullmatch(restored_sha)
                ):
                    errors.append(_error(
                        "bps.restoration_hash_missing",
                        "setpoint {0}: verified restoration SHA256 is unavailable".format(setpoint),
                    ))
                    stop_after_cycle = True
                else:
                    cycle.update({
                        "before_sha256": original_sha,
                        "after_sha256": restored_sha,
                    })
                    if restored_sha != original_sha:
                        errors.append(_error(
                            "bps.restoration_hash_mismatch",
                            "setpoint {0}: restoration SHA256 mismatch".format(setpoint),
                        ))
                        stop_after_cycle = True
                    else:
                        cycle["verdict"] = Verdict.PASS.value
                if pending_sample is not None and cycle["verdict"] == Verdict.PASS.value:
                    samples.append(pending_sample)
            except TransactionRestorationError as exc:
                if transaction is not None:
                    original_sha = transaction.original_sha256
                    restored_sha = transaction.restored_sha256
                    if isinstance(original_sha, str) and _SHA256_RE.fullmatch(original_sha):
                        cycle["before_sha256"] = original_sha
                    if isinstance(restored_sha, str) and _SHA256_RE.fullmatch(restored_sha):
                        cycle["after_sha256"] = restored_sha
                errors.append(_error("bps.restoration_failed", str(exc)))
                cycles.append(cycle)
                break
            except Exception as exc:
                errors.append(_error(
                    "bps.transaction_failed",
                    "setpoint {0}: {1}".format(setpoint, exc),
                ))
            cycles.append(cycle)
            if stop_after_cycle:
                break

        restoration_verdict = (
            Verdict.PASS.value
            if len(cycles) == len(BPS_SETPOINTS)
            and all(cycle["verdict"] == Verdict.PASS.value for cycle in cycles)
            else Verdict.ERROR.value
        )
        return {
            "schema_version": 1,
            "adapter_id": self.adapter_id,
            "run_id": context.run_id,
            "fixture": _FIXTURE_NAME,
            "samples": samples,
            "preconditions": preconditions,
            "restoration": {"cycles": cycles, "verdict": restoration_verdict},
            "errors": errors,
        }

    @staticmethod
    def _fallback_metric(observed_count: int) -> dict:
        return {
            "id": "evidence.observed_metric_count",
            "value": observed_count,
            "unit": "count",
            "baseline_value": 8,
            "rule": {"kind": "exact", "reference": 8},
            "delta": {"absolute": observed_count - 8, "percent": (observed_count - 8) / 8 * 100.0},
            "verdict": Verdict.PASS.value if observed_count == 8 else Verdict.FAIL.value,
        }

    def _metrics(self, raw: dict, baseline_gate: dict) -> List[dict]:
        if not isinstance(baseline_gate, dict) or not baseline_gate:
            raise EvidenceError("BPS baseline gate is missing")
        if baseline_gate.get("adapter_schema_version") != self.schema_version:
            raise EvidenceError("BPS baseline adapter schema mismatch")
        metrics = baseline_gate.get("metrics")
        if not isinstance(metrics, dict):
            raise EvidenceError("BPS baseline metrics are missing")
        actual_by_setpoint = {
            sample["setpoint_kbps"]: sample["actual_bps"] for sample in raw["samples"]
        }
        normalized = []
        for setpoint in BPS_SETPOINTS:
            if setpoint not in actual_by_setpoint:
                raise EvidenceError("BPS sample is missing for setpoint {0}".format(setpoint))
            for assertion in ("target", "baseline"):
                metric_id = "bps.ch0.{0}.{1}".format(setpoint, assertion)
                baseline_metric = metrics.get(metric_id)
                if not isinstance(baseline_metric, dict):
                    raise EvidenceError("BPS baseline metric is missing: {0}".format(metric_id))
                unit = baseline_metric.get("unit")
                result = evaluate_rule(actual_by_setpoint[setpoint], unit, baseline_metric)
                normalized.append({
                    "id": metric_id,
                    "value": actual_by_setpoint[setpoint],
                    "unit": unit,
                    **result,
                })
        if set(metrics) != {metric["id"] for metric in normalized}:
            raise EvidenceError("BPS baseline metric coverage mismatch")
        return normalized

    def run(self, context: AdapterContext) -> dict:
        raw = self.collect_raw(context)
        context.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = context.raw_dir / _RAW_NAME
        payload = _raw_bytes(raw)
        raw_path.write_bytes(payload)
        errors = copy.deepcopy(raw["errors"])
        metrics: List[dict]
        try:
            if errors:
                raise EvidenceError("raw BPS collection is incomplete")
            metrics = self._metrics(raw, context.baseline_gate)
        except EvidenceError as exc:
            errors.append(_error("bps.evaluation_error", str(exc)))
            metrics = [self._fallback_metric(len(raw["samples"]) * 2)]

        has_failed_metric = any(metric["verdict"] == Verdict.FAIL.value for metric in metrics)
        if errors or raw["restoration"]["verdict"] != Verdict.PASS.value:
            verdict = Verdict.ERROR.value
        elif has_failed_metric:
            verdict = Verdict.FAIL.value
        else:
            verdict = Verdict.PASS.value
        return {
            "id": self.adapter_id,
            "adapter_id": self.adapter_id,
            "adapter_schema_version": self.schema_version,
            "comparability": copy.deepcopy(context.baseline_gate.get("comparability", {}))
            if isinstance(context.baseline_gate, dict) else {},
            "process": {"exit_code": 0 if not errors else 1},
            "raw_output": {
                "path": "raw/{0}".format(_RAW_NAME),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            "preconditions": raw["preconditions"],
            "metrics": metrics,
            "restoration": raw["restoration"],
            "diagnostic_refs": ["raw/{0}".format(_RAW_NAME)],
            "errors": errors,
            "verdict": verdict,
        }


def evaluate_bps_gate(gate: dict, baseline_gate: dict) -> Verdict:
    """Run the shared fail-closed evaluator for one compatibility gate."""
    baseline_metrics = baseline_gate.get("metrics", {}) if isinstance(baseline_gate, dict) else {}
    normalized_baseline = {
        "id": "bps_quick",
        "adapter_id": "bps_quick",
        "adapter_schema_version": baseline_gate.get("adapter_schema_version")
        if isinstance(baseline_gate, dict) else None,
        "metrics": [dict(metric, id=metric_id) for metric_id, metric in baseline_metrics.items()],
    }
    document = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "deployment": {"mode": "predeployed", "verified": False},
        "gates": [gate],
        "verdict": gate.get("verdict", Verdict.ERROR.value),
    }
    return recompute_overall_verdict(document, {"gates": [normalized_baseline]})


def run_local_bps(output_path: Path) -> dict:
    """Run the adapter locally for the legacy entry point and centrally evaluate it."""
    baseline_path = Path(os.environ.get("PIM_HW_BASELINE", "baselines/hw-baseline.json"))
    loaded = load_baseline(baseline_path)
    profile = load_profile("profiles", _FIXTURE_NAME)
    target = profile.get("target", {})
    host = os.environ.get("TARGET_HOST", "192.168.214.4")
    ssh = SshClient(
        host,
        user=target.get("user", "root"),
        password=target.get("password", "root"),
    )
    try:
        gate = BpsAdapter().run(AdapterContext(
            ssh=ssh,
            baseline_gate=loaded.data["gates"]["bps_quick"],
            run_id="bps-quick-{0}".format(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")),
            raw_dir=output_path.parent / "raw",
        ))
        gate["verdict"] = evaluate_bps_gate(gate, loaded.data["gates"]["bps_quick"]).value
        return gate
    finally:
        ssh.close()
