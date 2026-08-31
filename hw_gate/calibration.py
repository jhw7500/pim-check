from __future__ import annotations

import copy
import json
import os
import re
import statistics
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Union

from hw_gate.baseline import validate_baseline
from hw_gate.rules import EvidenceError


_SETPOINTS = (1024, 2048, 4096, 8192)
_MAX_CANDIDATE_BYTES = 1_048_576
_PRODUCTION_BASELINE = Path(__file__).resolve().parents[1] / "baselines" / "hw-baseline.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def load_template(path: Union[str, Path]) -> dict:
    """Load the review template without accepting it as production policy."""
    template_path = Path(path)
    try:
        if template_path.stat().st_size > _MAX_CANDIDATE_BYTES:
            raise EvidenceError("calibration template exceeds 1,048,576 bytes")
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except EvidenceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("calibration template is unavailable or malformed") from exc
    validate_baseline(template, production=False)
    return template


def validate_candidate_output_path(path: Union[str, Path]) -> Path:
    """Return a resolved candidate path, refusing the production policy path."""
    output = Path(path)
    if output.resolve() == _PRODUCTION_BASELINE.resolve():
        raise EvidenceError("calibration refuses to write the production baseline")
    return output


def _materialized_identity(template_claim: Mapping[str, Any], observed: object) -> Dict[str, Any]:
    if not isinstance(observed, dict):
        raise EvidenceError("calibration identity claim is malformed")
    if observed.get("id") != template_claim.get("id") or observed.get("kind") != template_claim.get("kind"):
        raise EvidenceError("calibration identity claim does not match the template")
    kind = template_claim.get("kind")
    actual = observed.get("actual")
    if not isinstance(actual, str) or not actual:
        raise EvidenceError("calibration identity claim has no actual value")
    result: Dict[str, Any] = {"id": template_claim["id"], "kind": kind}
    if kind == "module_sha256":
        if observed.get("module") != template_claim.get("module"):
            raise EvidenceError("calibration module identity context changed")
        result.update({"module": template_claim["module"], "sha256": actual})
    elif kind == "module_version":
        if observed.get("module") != template_claim.get("module"):
            raise EvidenceError("calibration module identity context changed")
        result.update({"module": template_claim["module"], "version": actual})
    elif kind == "file_sha256":
        if observed.get("requested_path") != template_claim.get("path"):
            raise EvidenceError("calibration file identity context changed")
        result.update({"path": template_claim["path"], "sha256": actual})
    else:
        raise EvidenceError("calibration identity kind is unsupported")
    return result


def _run_identity(template: dict, run: Mapping[str, Any]) -> List[Dict[str, Any]]:
    observed_claims = run.get("identity")
    if not isinstance(observed_claims, list):
        raise EvidenceError("calibration run identity is missing")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not item["id"]
        for item in observed_claims
    ):
        raise EvidenceError("calibration run identity contains a malformed claim")
    identifiers = [item["id"] for item in observed_claims]
    if len(identifiers) != len(set(identifiers)):
        raise EvidenceError("calibration run identity contains duplicate claim IDs")
    template_claims = template["target_identity"]
    if set(identifiers) != {item["id"] for item in template_claims}:
        raise EvidenceError("calibration run identity coverage is incomplete")
    by_id = {item["id"]: item for item in observed_claims}
    return [_materialized_identity(item, by_id[item["id"]]) for item in template_claims]


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def build_candidate(template: dict, calibration_runs: Sequence[dict]) -> dict:
    """Build a review-only baseline candidate from three controlled raw runs."""
    validate_baseline(template, production=False)
    runs = copy.deepcopy(list(calibration_runs))
    candidate = {
        "schema_version": 1,
        "candidate_type": "hw-baseline",
        "eligible": False,
        "reasons": [],
        "analysis": {"setpoints": {}},
        "source_runs": runs,
        "baseline": None,
    }
    reasons: List[str] = candidate["reasons"]
    if len(runs) != 3:
        reasons.append("calibration requires exactly three independent runs")

    run_ids: List[str] = []
    samples_by_setpoint: Dict[int, List[int]] = {setpoint: [] for setpoint in _SETPOINTS}
    stable_identity: Union[None, List[Dict[str, Any]]] = None

    for index, run in enumerate(runs):
        label = "run {0}".format(index + 1)
        if not isinstance(run, dict):
            reasons.append("{0} is malformed".format(label))
            continue
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            reasons.append("{0} has no source run ID".format(label))
        else:
            run_ids.append(run_id)

        try:
            identity = _run_identity(template, run)
            if stable_identity is None:
                stable_identity = identity
            elif identity != stable_identity:
                reasons.append("{0} target identity differs from the other runs".format(label))
        except EvidenceError as exc:
            reasons.append("{0} identity: {1}".format(label, exc))

        raw = run.get("raw")
        if not isinstance(raw, dict):
            reasons.append("{0} raw controlled sample is missing".format(label))
            continue
        if raw.get("run_id") != run_id:
            reasons.append("{0} raw sample is not bound to its source run ID".format(label))
        if raw.get("adapter_id") != "bps_quick" or raw.get("fixture") != "multi_1ch_0_720p":
            reasons.append("{0} raw sample has incompatible comparability context".format(label))
        errors = raw.get("errors")
        if not isinstance(errors, list) or errors:
            reasons.append("{0} raw collection reported errors".format(label))
        restoration = raw.get("restoration")
        if not isinstance(restoration, dict) or restoration.get("verdict") != "PASS":
            reasons.append("{0} restoration was not verified".format(label))
        else:
            cycles = restoration.get("cycles")
            cycle_by_setpoint = {
                cycle.get("setpoint_kbps"): cycle
                for cycle in cycles
                if isinstance(cycle, dict) and cycle.get("setpoint_kbps") in _SETPOINTS
            } if isinstance(cycles, list) else {}
            if len(cycle_by_setpoint) != len(_SETPOINTS) or len(cycles) != len(_SETPOINTS):
                reasons.append("{0} restoration cycle coverage is incomplete".format(label))
            else:
                for setpoint in _SETPOINTS:
                    cycle = cycle_by_setpoint[setpoint]
                    before = cycle.get("before_sha256")
                    after = cycle.get("after_sha256")
                    if (
                        cycle.get("verdict") != "PASS"
                        or not isinstance(before, str)
                        or not _SHA256_RE.fullmatch(before)
                        or after != before
                    ):
                        reasons.append(
                            "{0} setpoint {1} restoration hash is not an exact verified match".format(
                                label, setpoint,
                            )
                        )

        samples = raw.get("samples")
        if not isinstance(samples, list):
            reasons.append("{0} samples are malformed".format(label))
            continue
        observed: Dict[int, int] = {}
        for sample in samples:
            if not isinstance(sample, dict):
                reasons.append("{0} contains a malformed sample".format(label))
                continue
            setpoint = sample.get("setpoint_kbps")
            actual = sample.get("actual_bps")
            if setpoint not in _SETPOINTS or setpoint in observed or not _positive_integer(actual):
                reasons.append("{0} contains a duplicate, unknown, or non-numeric setpoint".format(label))
                continue
            observed[setpoint] = actual
        for setpoint in _SETPOINTS:
            if setpoint not in observed:
                reasons.append("{0} is missing setpoint {1}".format(label, setpoint))
            else:
                samples_by_setpoint[setpoint].append(observed[setpoint])

    if len(run_ids) != len(set(run_ids)):
        reasons.append("calibration samples must have three independent source run IDs")

    medians: Dict[int, Union[int, float]] = {}
    for setpoint in _SETPOINTS:
        samples = samples_by_setpoint[setpoint]
        target = setpoint * 1000
        setpoint_reasons: List[str] = []
        median: Union[None, int, float] = None
        target_deviations: List[float] = []
        median_deviations: List[float] = []
        if len(samples) != 3:
            setpoint_reasons.append("setpoint {0} requires exactly three samples".format(setpoint))
        else:
            median = statistics.median(samples)
            target_deviations = [abs(sample - target) / target * 100.0 for sample in samples]
            median_deviations = [abs(sample - median) / median * 100.0 for sample in samples]
            if any(deviation > 10.0 for deviation in target_deviations):
                setpoint_reasons.append("setpoint {0} has a sample outside the 10% target limit".format(setpoint))
            if max(median_deviations) > 5.0:
                setpoint_reasons.append("setpoint {0} exceeds the 5% sample-to-median limit".format(setpoint))
            medians[setpoint] = median
        candidate["analysis"]["setpoints"][str(setpoint)] = {
            "target_bps": target,
            "samples": samples,
            "median": median,
            "max_target_percent": max(target_deviations) if target_deviations else None,
            "max_sample_to_median_percent": max(median_deviations) if median_deviations else None,
            "eligible": not setpoint_reasons,
            "reasons": setpoint_reasons,
        }
        reasons.extend(setpoint_reasons)

    if reasons or stable_identity is None:
        if stable_identity is None and not any("identity" in reason for reason in reasons):
            reasons.append("calibration target identity is not populated")
        return candidate

    baseline = copy.deepcopy(template)
    baseline["target_identity"] = stable_identity
    baseline["calibration"]["bps"] = {
        "source_run_ids": run_ids,
        "samples": {str(setpoint): samples_by_setpoint[setpoint] for setpoint in _SETPOINTS},
    }
    metrics = baseline["gates"]["bps_quick"]["metrics"]
    for setpoint in _SETPOINTS:
        metric = metrics["bps.ch0.{0}.baseline".format(setpoint)]
        metric.pop("calibration_required", None)
        metric["value"] = medians[setpoint]
        metric["rule"]["reference"] = medians[setpoint]
    try:
        validate_baseline(baseline, production=True)
    except EvidenceError as exc:
        reasons.append("materialized baseline is invalid: {0}".format(exc))
        return candidate
    candidate["eligible"] = True
    candidate["baseline"] = baseline
    return candidate


def write_candidate(path: Union[str, Path], candidate: dict) -> None:
    """Atomically write one private review artifact and never production policy."""
    output = validate_candidate_output_path(path)
    payload = (json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > _MAX_CANDIDATE_BYTES:
        raise EvidenceError("calibration candidate exceeds 1,048,576 bytes")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".{0}.".format(output.name), dir=str(output.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
