from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

from checks.target_identity import TargetIdentityCheck
from hw_gate.adapters.base import AdapterContext
from hw_gate.adapters.bps import BpsAdapter
from hw_gate.adapters.mixed_combo import MixedComboAdapter
from hw_gate.baseline import LoadedBaseline, load_baseline
from hw_gate.calibration import (
    build_candidate,
    load_template,
    validate_candidate_output_path,
    write_candidate,
)
from hw_gate.diagnostics import bounded_diagnostic_text, collect_diagnostics
from hw_gate.evidence import recompute_overall_verdict, validate_structure
from hw_gate.render import render_markdown
from hw_gate.rules import EvidenceError, Verdict
from hw_gate.termination import TerminationRequested, installed_termination_handlers
from hw_gate.transaction import recover_pending_transaction
from setup import SetupManager
from ssh import SshClient


PASS_EXIT = 0
FAIL_EXIT = 1
ERROR_EXIT = 2
BUSY_EXIT = 4
MAX_EVIDENCE_BYTES = 1_048_576
TARGET_HOST = "192.168.214.4"
ADAPTER_ORDER = ("bps_quick", "mixed_combo")
ADAPTER_FACTORIES: Dict[str, Callable[[], object]] = {
    "bps_quick": BpsAdapter,
    "mixed_combo": MixedComboAdapter,
}
PROCESS_NAMES = ("gstApp", "pim-service")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _repository(value: str) -> str:
    if not _REPOSITORY_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("repository must use owner/name format")
    return value


def _positive(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _full_sha(value: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("value must be a full lowercase commit SHA")
    return value


def _target_host(value: str) -> str:
    if value != TARGET_HOST:
        raise argparse.ArgumentTypeError("target host must be {0}".format(TARGET_HOST))
    return value


def _gate_selection(value: str) -> tuple[str, ...]:
    gates = tuple(value.split(","))
    if not gates or any(not gate for gate in gates):
        raise argparse.ArgumentTypeError("gates must not be empty")
    if len(gates) != len(set(gates)):
        raise argparse.ArgumentTypeError("gates must not contain duplicates")
    if any(gate not in ADAPTER_ORDER for gate in gates):
        raise argparse.ArgumentTypeError("gates contain an unknown adapter")
    selected = set(gates)
    return tuple(gate for gate in ADAPTER_ORDER if gate in selected)


def _child_exit_code(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("child exit code must be an integer") from exc
    if parsed < 0 or parsed > 255:
        raise argparse.ArgumentTypeError("child exit code must be between 0 and 255")
    return parsed


def _three_repetitions(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("calibration repetitions must be exactly 3") from exc
    if parsed != 3:
        raise argparse.ArgumentTypeError("calibration repetitions must be exactly 3")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m hw_gate", description="Durable predeployed hardware evidence")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="prepare a trusted HEAD-bound run envelope")
    prepare.add_argument("--repository", type=_repository, required=True)
    prepare.add_argument("--pr-number", type=_positive, required=True)
    prepare.add_argument("--pr-head-sha", type=_full_sha, required=True)
    prepare.add_argument("--workflow-run-id", type=_positive, required=True)
    prepare.add_argument("--workflow-run-attempt", type=_positive, required=True)
    prepare.add_argument("--source-commit", type=_full_sha, required=True)
    prepare.add_argument("--baseline", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.set_defaults(handler=_prepare_command)

    measure = commands.add_parser("measure", help="measure the fixed leased target")
    measure.add_argument("--envelope", type=Path, required=True)
    measure.add_argument("--target-host", type=_target_host, required=True)
    measure.add_argument("--output-dir", type=Path, required=True)
    measure.add_argument("--gates", type=_gate_selection, default=ADAPTER_ORDER)
    measure.set_defaults(handler=_measure_command)

    finalize = commands.add_parser("finalize", help="finalize wrapper or partial-child evidence")
    finalize.add_argument("--envelope", type=Path, required=True)
    finalize.add_argument("--output-dir", type=Path, required=True)
    finalize.add_argument("--child-exit-code", type=_child_exit_code, required=True)
    finalize.set_defaults(handler=_finalize_command)

    validate = commands.add_parser("validate", help="validate and centrally recompute evidence")
    validate.add_argument("--evidence", type=Path, required=True)
    validate.add_argument("--baseline", type=Path, required=True)
    validate.set_defaults(handler=_validate_command)

    calibrate = commands.add_parser("calibrate", help="build a review-only three-run baseline candidate")
    calibrate.add_argument("--template", type=Path, required=True)
    calibrate.add_argument("--target-host", type=_target_host, required=True)
    calibrate.add_argument("--repetitions", type=_three_repetitions, required=True)
    calibrate.add_argument("--output", type=Path, required=True)
    calibrate.set_defaults(handler=_calibrate_command)
    return parser


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes, *, max_bytes: Optional[int] = None) -> None:
    if max_bytes is not None and len(payload) > max_bytes:
        raise EvidenceError("evidence JSON exceeds 1,048,576 bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".{0}.".format(path.name), dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
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


def atomic_write_json(path: Path, payload: dict, *, max_bytes: int = MAX_EVIDENCE_BYTES) -> None:
    _atomic_write(path, _json_bytes(payload), max_bytes=max_bytes)


def atomic_write_text(path: Path, payload: str) -> None:
    _atomic_write(path, payload.encode("utf-8"))


def _load_json(path: Path, *, max_bytes: int = MAX_EVIDENCE_BYTES) -> dict:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise EvidenceError("evidence JSON exceeds 1,048,576 bytes")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except EvidenceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("JSON artifact is unavailable or malformed: {0}".format(path)) from exc
    if not isinstance(payload, dict):
        raise EvidenceError("JSON artifact must be an object")
    return payload


def _validate_envelope(envelope: dict) -> None:
    if envelope.get("schema_version") != 1:
        raise EvidenceError("envelope schema_version must be 1")
    run = envelope.get("run")
    baseline = envelope.get("baseline")
    if not isinstance(run, dict) or not isinstance(baseline, dict):
        raise EvidenceError("envelope run and baseline bindings are required")
    if not isinstance(run.get("repository"), str) or not _REPOSITORY_RE.fullmatch(run["repository"]):
        raise EvidenceError("envelope repository is malformed")
    for name in ("pr_number", "workflow_run_id", "workflow_run_attempt"):
        if isinstance(run.get(name), bool) or not isinstance(run.get(name), int) or run[name] <= 0:
            raise EvidenceError("envelope {0} must be positive".format(name))
    if not isinstance(run.get("pr_head_sha"), str) or not _SHA_RE.fullmatch(run["pr_head_sha"]):
        raise EvidenceError("envelope PR HEAD is malformed")
    expected_url = "https://github.com/{0}/actions/runs/{1}/attempts/{2}".format(
        run["repository"], run["workflow_run_id"], run["workflow_run_attempt"],
    )
    if run.get("run_url") != expected_url:
        raise EvidenceError("envelope run URL is not bound to its workflow identity")
    if not isinstance(envelope.get("source_commit"), str) or not _SHA_RE.fullmatch(envelope["source_commit"]):
        raise EvidenceError("envelope source commit is malformed")
    if not isinstance(baseline.get("source_commit"), str) or not _SHA_RE.fullmatch(baseline["source_commit"]):
        raise EvidenceError("envelope baseline source commit is malformed")
    if not isinstance(baseline.get("sha256"), str) or not _SHA256_RE.fullmatch(baseline["sha256"]):
        raise EvidenceError("envelope baseline SHA256 is malformed")
    if not isinstance(baseline.get("path"), str) or not baseline["path"]:
        raise EvidenceError("envelope baseline path is missing")


def _load_bound_envelope(envelope_path: Path) -> dict:
    envelope = _load_json(envelope_path)
    _validate_envelope(envelope)
    head = envelope["run"]["pr_head_sha"]
    if envelope_path.name != head + ".candidate.json":
        raise EvidenceError("envelope path is not bound to the PR HEAD")
    return envelope


def _load_bound_baseline(envelope: dict) -> LoadedBaseline:
    loaded = load_baseline(envelope["baseline"]["path"])
    if loaded.sha256 != envelope["baseline"]["sha256"]:
        raise EvidenceError("baseline bytes changed after envelope preparation")
    if loaded.data["source_commit"] != envelope["baseline"]["source_commit"]:
        raise EvidenceError("baseline source commit changed after envelope preparation")
    return loaded


def _bound_envelope(envelope_path: Path) -> tuple[dict, LoadedBaseline]:
    envelope = _load_bound_envelope(envelope_path)
    loaded = _load_bound_baseline(envelope)
    return envelope, loaded


def _prepare_command(args: argparse.Namespace) -> int:
    loaded = load_baseline(args.baseline)
    run = {
        "repository": args.repository,
        "pr_number": args.pr_number,
        "pr_head_sha": args.pr_head_sha,
        "workflow_run_id": args.workflow_run_id,
        "workflow_run_attempt": args.workflow_run_attempt,
        "run_url": "https://github.com/{0}/actions/runs/{1}/attempts/{2}".format(
            args.repository, args.workflow_run_id, args.workflow_run_attempt,
        ),
    }
    envelope = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "run": run,
        "source_commit": args.source_commit,
        "baseline": {
            "path": str(args.baseline),
            "sha256": loaded.sha256,
            "source_commit": loaded.data["source_commit"],
        },
    }
    atomic_write_json(args.output_dir / (args.pr_head_sha + ".candidate.json"), envelope)
    return PASS_EXIT


def _fallback_metric(metric_id: str, value: int, unit: str) -> dict:
    return {
        "id": metric_id,
        "value": value,
        "unit": unit,
        "baseline_value": 0,
        "rule": {"kind": "exact", "reference": 0},
        "delta": {"absolute": value, "percent": 0},
        "verdict": Verdict.PASS.value if value == 0 else Verdict.FAIL.value,
    }


def _infrastructure_gate(head: str, code: str, message: str, *, metric_id: str, value: int, unit: str) -> dict:
    raw_path = "raw/{0}/infrastructure.json".format(head)
    bounded_message = message.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
    return {
        "id": "infrastructure",
        "adapter_id": "infrastructure",
        "adapter_schema_version": 1,
        "comparability": {},
        "process": {"exit_code": value},
        "raw_output": {"path": raw_path, "sha256": "0" * 64},
        "identity": {"verdict": Verdict.ERROR.value},
        "preconditions": [],
        "metrics": [_fallback_metric(metric_id, value, unit)],
        "restoration": {"verdict": Verdict.ERROR.value},
        "diagnostic_refs": [raw_path],
        "errors": [{"code": code, "message": bounded_message}],
        "verdict": Verdict.ERROR.value,
    }


def _new_document(envelope: dict, loaded: Optional[LoadedBaseline], target_host: str) -> dict:
    started_at = _utc_now()
    run = dict(envelope["run"])
    run["started_at"] = started_at
    return {
        "schema_version": 1,
        "created_at": started_at,
        "run": run,
        "source_commit": envelope["source_commit"],
        "board": {"id": "pim", "target_host": target_host, "identity": []},
        "baseline": dict(envelope["baseline"]),
        "comparability": dict(loaded.data["comparability"]) if loaded is not None else {},
        "deployment": {"mode": "predeployed", "verified": False, "artifacts": []},
        "gates": [_infrastructure_gate(
            run["pr_head_sha"], "infrastructure.pending", "measurement has not completed",
            metric_id="evidence.observed_metric_count", value=0, unit="count",
        )],
        "diagnostics": [],
        "lifecycle": {"state": "created"},
        "verdict": Verdict.ERROR.value,
        "overall_verdict": Verdict.ERROR.value,
    }


def _result_paths(output_dir: Path, head: str) -> tuple[Path, Path, Path]:
    return output_dir / (head + ".json"), output_dir / (head + ".md"), output_dir / "raw" / head


def _checkpoint(document: dict, loaded: LoadedBaseline, output_dir: Path, state: str) -> None:
    document["lifecycle"] = {"state": state}
    _persist_infrastructure_raw(document, output_dir)
    verdict = recompute_overall_verdict(document, loaded)
    document["verdict"] = verdict.value
    document["overall_verdict"] = verdict.value
    head = document["run"]["pr_head_sha"]
    json_path, markdown_path, _ = _result_paths(output_dir, head)
    atomic_write_json(json_path, document)
    atomic_write_text(markdown_path, render_markdown(document))


def _persist_infrastructure_raw(document: dict, output_dir: Path) -> None:
    for gate in document.get("gates", []):
        if not isinstance(gate, dict) or gate.get("id") != "infrastructure":
            continue
        raw_output = gate.get("raw_output")
        if not isinstance(raw_output, dict) or not isinstance(raw_output.get("path"), str):
            continue
        payload = _json_bytes({
            "errors": gate.get("errors", []),
            "metrics": gate.get("metrics", []),
            "process": gate.get("process", {}),
        })
        raw_path = output_dir / raw_output["path"]
        _atomic_write(raw_path, payload)
        raw_output["sha256"] = hashlib.sha256(payload).hexdigest()


def _set_infrastructure_error(document: dict, code: str, message: str, *, value: int = 0) -> None:
    head = document["run"]["pr_head_sha"]
    document["gates"] = [_infrastructure_gate(
        head, code, message, metric_id="evidence.observed_metric_count", value=value, unit="count",
    )]


def _normalize_gate_paths(gate: dict, head: str) -> None:
    raw_output = gate.get("raw_output")
    if isinstance(raw_output, dict) and isinstance(raw_output.get("path"), str):
        raw_output["path"] = "raw/{0}/{1}".format(head, Path(raw_output["path"]).name)
    refs = gate.get("diagnostic_refs")
    if isinstance(refs, list):
        gate["diagnostic_refs"] = ["raw/{0}/{1}".format(head, Path(ref).name) for ref in refs if isinstance(ref, str)]


def _validate_adapter_gate(gate: object, adapter_id: str, schema_version: int) -> None:
    if not isinstance(gate, dict):
        raise EvidenceError("adapter {0} returned non-object evidence".format(adapter_id))
    if gate.get("id") != adapter_id or gate.get("adapter_id") != adapter_id:
        raise EvidenceError("adapter {0} returned mismatched identity".format(adapter_id))
    if gate.get("adapter_schema_version") != schema_version:
        raise EvidenceError("adapter {0} returned mismatched schema version".format(adapter_id))
    validate_structure({
        "schema_version": 1,
        "created_at": _utc_now(),
        "deployment": {"mode": "predeployed", "verified": False},
        "gates": [gate],
        "verdict": Verdict.ERROR.value,
    })


def _collect_terminal_diagnostics(document: dict, ssh: object, raw_dir: Path) -> None:
    try:
        document["diagnostics"] = collect_diagnostics(ssh, raw_dir, PROCESS_NAMES)
    except Exception as exc:
        document["diagnostics"] = [{
            "id": "diagnostics.error",
            "output": bounded_diagnostic_text(str(exc)),
        }]


def _record_termination(document: dict, exc: TerminationRequested) -> None:
    _set_infrastructure_error(
        document, "infrastructure.signal", "terminated by signal {0}".format(exc.signum),
    )


def _checkpoint_protected(
    document: dict,
    loaded: LoadedBaseline,
    output_dir: Path,
    state: str,
) -> None:
    while True:
        try:
            _checkpoint(document, loaded, output_dir, state)
            return
        except TerminationRequested as exc:
            _record_termination(document, exc)


def _close_ssh_protected(
    ssh: object,
    document: dict,
    loaded: LoadedBaseline,
    output_dir: Path,
) -> None:
    while True:
        try:
            ssh.close()  # type: ignore[attr-defined]
            return
        except TerminationRequested as exc:
            _record_termination(document, exc)
            _checkpoint_protected(document, loaded, output_dir, "terminated")
        except Exception as exc:
            _set_infrastructure_error(document, "infrastructure.ssh_close", str(exc))
            _checkpoint_protected(document, loaded, output_dir, "close_error")
            return


def _finish_run_protected(document: dict, loaded: LoadedBaseline, output_dir: Path) -> None:
    while True:
        try:
            document["run"]["finished_at"] = _utc_now()
            _checkpoint(document, loaded, output_dir, "complete")
            return
        except TerminationRequested as exc:
            _record_termination(document, exc)


def _measure_command(args: argparse.Namespace) -> int:
    try:
        envelope, loaded = _bound_envelope(args.envelope)
        if loaded.data["comparability"]["target_host"] != args.target_host:
            raise EvidenceError("target host does not match baseline comparability")
    except EvidenceError as exc:
        print("hw_gate measure: {0}".format(exc), file=sys.stderr)
        return ERROR_EXIT

    document = _new_document(envelope, loaded, args.target_host)
    head = envelope["run"]["pr_head_sha"]
    _, _, raw_dir = _result_paths(args.output_dir, head)
    raw_dir.mkdir(parents=True, exist_ok=True)
    _checkpoint(document, loaded, args.output_dir, "baseline_validated")

    ssh: Optional[object] = None
    with installed_termination_handlers():
        try:
            ssh = SshClient(args.target_host)
            manager = SetupManager(ssh)
            try:
                recover_pending_transaction(manager)
                _checkpoint(document, loaded, args.output_dir, "recovery_complete")

                identity_check = TargetIdentityCheck()
                identity_config = {"target_identity": loaded.data["target_identity"]}
                identity = identity_check.collect(ssh, identity_config)
                document["board"]["identity"] = identity.get("claims", []) if isinstance(identity, dict) else []
                identity_valid, identity_reason = identity_check.validate(identity, identity_config)
                if not identity_valid:
                    _set_infrastructure_error(document, "infrastructure.identity", identity_reason)
                    _checkpoint_protected(document, loaded, args.output_dir, "identity_error")
                else:
                    _checkpoint(document, loaded, args.output_dir, "identity_verified")
                    document["gates"] = []
                    for adapter_id in args.gates:
                        adapter = ADAPTER_FACTORIES[adapter_id]()
                        gate = adapter.run(AdapterContext(
                            ssh=ssh,
                            baseline_gate=loaded.data["gates"][adapter_id],
                            run_id="{0}-{1}".format(head, adapter_id),
                            raw_dir=raw_dir,
                        ))
                        try:
                            if not isinstance(gate, dict):
                                raise EvidenceError("adapter {0} returned non-object evidence".format(adapter_id))
                            gate["identity"] = {"verdict": Verdict.PASS.value}
                            _normalize_gate_paths(gate, head)
                            _validate_adapter_gate(gate, adapter_id, adapter.schema_version)
                        except EvidenceError as exc:
                            _set_infrastructure_error(document, "infrastructure.adapter_evidence", str(exc))
                            _checkpoint_protected(document, loaded, args.output_dir, "adapter_evidence_error")
                            break
                        document["gates"].append(gate)
                        _checkpoint(document, loaded, args.output_dir, "adapter:{0}".format(adapter_id))
            except TerminationRequested as exc:
                _record_termination(document, exc)
                _checkpoint_protected(document, loaded, args.output_dir, "terminated")
            except Exception as exc:
                phase = document.get("lifecycle", {}).get("state", "measurement")
                code = "infrastructure.recovery" if phase == "baseline_validated" else "infrastructure.measurement"
                _set_infrastructure_error(document, code, str(exc))
                _checkpoint_protected(document, loaded, args.output_dir, "error")
        except TerminationRequested as exc:
            _record_termination(document, exc)
            _checkpoint_protected(document, loaded, args.output_dir, "terminated")
        except Exception as exc:
            code = "infrastructure.ssh" if ssh is None else "infrastructure.setup"
            _set_infrastructure_error(document, code, str(exc))
            _checkpoint_protected(document, loaded, args.output_dir, "target_error")

        try:
            if ssh is not None:
                _collect_terminal_diagnostics(document, ssh, raw_dir)
            else:
                document["diagnostics"] = [{"id": "ssh.unavailable", "output": "target connection was not established"}]
        except TerminationRequested as exc:
            _record_termination(document, exc)
            document["diagnostics"] = [{"id": "diagnostics.interrupted", "output": str(exc)}]
        _checkpoint_protected(document, loaded, args.output_dir, "diagnostics_complete")

        if ssh is not None:
            _close_ssh_protected(ssh, document, loaded, args.output_dir)

        _finish_run_protected(document, loaded, args.output_dir)
    return _exit_for_verdict(Verdict(document["verdict"]))


def _document_binding_matches(document: dict, envelope: dict) -> bool:
    run = document.get("run")
    baseline = document.get("baseline")
    if not isinstance(run, dict) or not isinstance(baseline, dict):
        return False
    expected_run = envelope["run"]
    return document.get("source_commit") == envelope["source_commit"] and all(
        run.get(name) == expected_run[name] for name in expected_run
    ) and all(
        baseline.get(name) == envelope["baseline"][name] for name in ("sha256", "source_commit", "path")
    )


def _finalizer_document(
    envelope: dict,
    loaded: Optional[LoadedBaseline],
    child_exit: int,
    busy: bool,
    preflight_error: Optional[str] = None,
) -> dict:
    target_host = loaded.data["comparability"]["target_host"] if loaded is not None else TARGET_HOST
    document = _new_document(envelope, loaded, target_host)
    document["run"]["finished_at"] = _utc_now()
    if busy:
        document["board"]["lease_exit_code"] = BUSY_EXIT
        document["gates"] = []
        document["lifecycle"] = {"state": "busy"}
        document["verdict"] = Verdict.BUSY.value
        document["overall_verdict"] = Verdict.BUSY.value
        return document
    message = "wrapper exited {0} without a valid child artifact".format(child_exit)
    if preflight_error:
        message += "; preflight: {0}".format(preflight_error)
    document["gates"] = [_infrastructure_gate(
        envelope["run"]["pr_head_sha"],
        "infrastructure.child_exit",
        message,
        metric_id="infrastructure.child_exit_code",
        value=child_exit,
        unit="exit_code",
    )]
    document["lifecycle"] = {"state": "finalizer_error"}
    return document


def _finalize_command(args: argparse.Namespace) -> int:
    try:
        envelope = _load_bound_envelope(args.envelope)
    except EvidenceError as exc:
        print("hw_gate finalize: {0}".format(exc), file=sys.stderr)
        return ERROR_EXIT
    loaded: Optional[LoadedBaseline] = None
    preflight_error: Optional[str] = None
    try:
        loaded = _load_bound_baseline(envelope)
    except EvidenceError as exc:
        preflight_error = str(exc)
    head = envelope["run"]["pr_head_sha"]
    json_path, markdown_path, _ = _result_paths(args.output_dir, head)
    child_exists = json_path.exists()
    if child_exists and loaded is not None:
        try:
            document = _load_json(json_path)
            validate_structure(document)
            if not _document_binding_matches(document, envelope):
                raise EvidenceError("child artifact binding does not match envelope")
            verdict = recompute_overall_verdict(document, loaded)
            declared = document.get("verdict")
            overall = document.get("overall_verdict", declared)
            if declared != verdict.value or overall != verdict.value:
                raise EvidenceError("child declared verdict disagrees with trusted recomputation")
            atomic_write_text(markdown_path, render_markdown(document))
            return _exit_for_verdict(verdict)
        except EvidenceError as exc:
            preflight_error = str(exc)

    busy = args.child_exit_code == BUSY_EXIT and not child_exists
    document = _finalizer_document(envelope, loaded, args.child_exit_code, busy, preflight_error)
    if not busy:
        verdict = recompute_overall_verdict(document, loaded)
        document["verdict"] = verdict.value
        document["overall_verdict"] = verdict.value
        _persist_infrastructure_raw(document, args.output_dir)
    atomic_write_json(json_path, document)
    atomic_write_text(markdown_path, render_markdown(document))
    return BUSY_EXIT if busy else ERROR_EXIT


def _validate_command(args: argparse.Namespace) -> int:
    try:
        document = _load_json(args.evidence)
        loaded = load_baseline(args.baseline)
        validate_structure(document)
        binding = document.get("baseline")
        if isinstance(binding, dict) and (
            binding.get("sha256") != loaded.sha256 or binding.get("source_commit") != loaded.data["source_commit"]
        ):
            raise EvidenceError("evidence baseline binding mismatch")
        verdict = recompute_overall_verdict(document, loaded)
        declared = document.get("verdict")
        overall = document.get("overall_verdict", declared)
        if declared != verdict.value or overall != verdict.value:
            return ERROR_EXIT
        return _exit_for_verdict(verdict)
    except EvidenceError as exc:
        print("hw_gate validate: {0}".format(exc), file=sys.stderr)
        return ERROR_EXIT


def _calibration_run_id(index: int) -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return "baseline-calibration-{0}-{1}".format(timestamp, index)


def _calibrate_command(args: argparse.Namespace) -> int:
    try:
        output = validate_candidate_output_path(args.output)
        template = load_template(args.template)
        if template["comparability"]["target_host"] != args.target_host:
            raise EvidenceError("target host does not match calibration comparability")
    except EvidenceError as exc:
        print("hw_gate calibrate: {0}".format(exc), file=sys.stderr)
        return ERROR_EXIT

    calibration_runs = []
    ssh: Optional[object] = None
    result = ERROR_EXIT
    try:
        with installed_termination_handlers():
            ssh = SshClient(args.target_host)
            manager = SetupManager(ssh)
            recover_pending_transaction(manager)
            identity_check = TargetIdentityCheck()
            adapter = BpsAdapter()
            for index in range(1, args.repetitions + 1):
                run_id = _calibration_run_id(index)
                identity_evidence = identity_check.collect(
                    ssh, {"target_identity": template["target_identity"]},
                )
                claims = identity_evidence.get("claims") if isinstance(identity_evidence, dict) else None
                errors = identity_evidence.get("errors") if isinstance(identity_evidence, dict) else None
                if not isinstance(claims, list) or not claims or not isinstance(errors, list) or errors:
                    identity_errors = (
                        [str(error) for error in errors]
                        if isinstance(errors, list) and errors
                        else ["target identity collection returned malformed or empty evidence"]
                    )
                    calibration_runs.append({
                        "run_id": run_id,
                        "identity": claims if isinstance(claims, list) else [],
                        "identity_errors": identity_errors,
                        "raw": None,
                    })
                    break
                raw = adapter.collect_raw(AdapterContext(
                    ssh=ssh,
                    baseline_gate={},
                    run_id=run_id,
                    raw_dir=output.parent,
                ))
                calibration_runs.append({
                    "run_id": run_id,
                    "identity": claims,
                    "identity_errors": [],
                    "raw": raw,
                })
                if raw.get("errors") or raw.get("restoration", {}).get("verdict") != Verdict.PASS.value:
                    break
        candidate = build_candidate(template, calibration_runs)
        write_candidate(output, candidate)
        result = PASS_EXIT if candidate["eligible"] else ERROR_EXIT
    except TerminationRequested as exc:
        print("hw_gate calibrate: {0}".format(exc), file=sys.stderr)
    except Exception as exc:
        print("hw_gate calibrate: {0}".format(exc), file=sys.stderr)
    finally:
        if ssh is not None:
            try:
                ssh.close()  # type: ignore[attr-defined]
            except Exception as exc:
                print("hw_gate calibrate: SSH close failed: {0}".format(exc), file=sys.stderr)
                result = ERROR_EXIT
    return result


def _exit_for_verdict(verdict: Verdict) -> int:
    return {
        Verdict.PASS: PASS_EXIT,
        Verdict.FAIL: FAIL_EXIT,
        Verdict.ERROR: ERROR_EXIT,
        Verdict.BUSY: BUSY_EXIT,
    }[verdict]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except EvidenceError as exc:
        print("hw_gate: {0}".format(exc), file=sys.stderr)
        return ERROR_EXIT
