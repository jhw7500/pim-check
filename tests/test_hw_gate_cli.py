from __future__ import annotations

import copy
import json
import signal
from pathlib import Path

import pytest

from hw_gate.rules import EvidenceError


FIXTURES = Path(__file__).parent / "fixtures" / "hw_gate"
FULL_SHA = "a" * 40
SOURCE_SHA = "c" * 40
BASELINE_SOURCE_SHA = "0" * 40


def _prepare_args(tmp_path: Path, **overrides: object) -> list[str]:
    values = {
        "repository": "jhw7500/pim-check",
        "pr_number": "115",
        "pr_head_sha": FULL_SHA,
        "workflow_run_id": "12345",
        "workflow_run_attempt": "2",
        "source_commit": SOURCE_SHA,
        "baseline": str(FIXTURES / "baseline.json"),
        "output_dir": str(tmp_path / "hw-results"),
    }
    values.update(overrides)
    args = ["prepare"]
    for name, value in values.items():
        args.extend(["--" + name.replace("_", "-"), str(value)])
    return args


def _prepare(tmp_path: Path) -> tuple[Path, Path]:
    from hw_gate.cli import main

    output_dir = tmp_path / "hw-results"
    assert main(_prepare_args(tmp_path)) == 0
    return output_dir / (FULL_SHA + ".candidate.json"), output_dir


def _metric(metric_id: str = "evidence.observed_metric_count", value: int = 1) -> dict:
    return {
        "id": metric_id,
        "value": value,
        "unit": "count",
        "baseline_value": 0,
        "rule": {"kind": "exact", "reference": 0},
        "delta": {"absolute": value, "percent": 0},
        "verdict": "FAIL" if value else "PASS",
    }


def _gate(gate_id: str) -> dict:
    return {
        "id": gate_id,
        "adapter_id": gate_id,
        "adapter_schema_version": 1,
        "comparability": {},
        "process": {"exit_code": 0},
        "raw_output": {"path": "raw/" + gate_id + ".json", "sha256": "0" * 64},
        "identity": {"verdict": "PASS"},
        "preconditions": [],
        "metrics": [_metric()],
        "restoration": {"verdict": "PASS"},
        "diagnostic_refs": [],
        "errors": [],
        "verdict": "FAIL",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "pim-check"),
        ("repository", "owner/repo/extra"),
        ("pr_number", "0"),
        ("pr_number", "-1"),
        ("workflow_run_id", "0"),
        ("workflow_run_attempt", "0"),
        ("pr_head_sha", "abc123"),
        ("pr_head_sha", "A" * 40),
        ("source_commit", "f" * 39),
    ],
)
def test_prepare_rejects_untrusted_run_identity(tmp_path: Path, field: str, value: str) -> None:
    """Malformed provenance must fail before an envelope can become trusted input."""
    from hw_gate.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(_prepare_args(tmp_path, **{field: value}))

    assert exc_info.value.code == 2


def test_cli_requires_every_prepare_field_and_rejects_unknown_arguments(tmp_path: Path) -> None:
    """Defaults or ignored flags must not create an under-bound envelope."""
    from hw_gate.cli import main

    missing = _prepare_args(tmp_path)
    del missing[-2:]
    with pytest.raises(SystemExit) as missing_error:
        main(missing)
    assert missing_error.value.code == 2

    with pytest.raises(SystemExit) as unknown_error:
        main(_prepare_args(tmp_path) + ["--surprise"])
    assert unknown_error.value.code == 2


@pytest.mark.parametrize("host", ["localhost", "192.168.0.6", "192.168.0.5 "])
def test_measure_accepts_only_the_fixed_target_host(tmp_path: Path, host: str) -> None:
    """A caller must not redirect trusted measurement to another SSH target."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        main([
            "measure", "--envelope", str(envelope), "--target-host", host,
            "--output-dir", str(output_dir),
        ])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("gates", ["bps_quick,unknown", "mixed_combo,bps_quick,bps_quick", ""])
def test_measure_rejects_unknown_duplicate_or_empty_gate_selection(tmp_path: Path, gates: str) -> None:
    """The adapter matrix must be an explicit subset of the committed deterministic order."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        main([
            "measure", "--envelope", str(envelope), "--target-host", "192.168.0.5",
            "--output-dir", str(output_dir), "--gates", gates,
        ])
    assert exc_info.value.code == 2


def test_prepare_writes_only_head_bound_mode_600_candidate(tmp_path: Path) -> None:
    """A prepared run must be durable, private, and bound to exact baseline bytes."""
    envelope_path, output_dir = _prepare(tmp_path)

    assert sorted(path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*")) == [
        FULL_SHA + ".candidate.json"
    ]
    assert envelope_path.stat().st_mode & 0o777 == 0o600
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert envelope["run"] == {
        "repository": "jhw7500/pim-check",
        "pr_number": 115,
        "pr_head_sha": FULL_SHA,
        "workflow_run_id": 12345,
        "workflow_run_attempt": 2,
        "run_url": "https://github.com/jhw7500/pim-check/actions/runs/12345/attempts/2",
    }
    assert envelope["source_commit"] == SOURCE_SHA
    assert envelope["baseline"]["source_commit"] == BASELINE_SOURCE_SHA
    assert len(envelope["baseline"]["sha256"]) == 64


class _FakeSsh:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        events.append("ssh-open")

    def close(self) -> None:
        self.events.append("ssh-close")


class _FakeIdentity:
    def __init__(self, events: list[str], valid: bool = True) -> None:
        self.events = events
        self.valid = valid

    def collect(self, ssh: object, config: dict) -> dict:
        self.events.append("identity")
        return {"claims": [{"id": "board", "actual": "measured<id>"}], "errors": []}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        return self.valid, "OK" if self.valid else "identity mismatch"


class _FakeAdapter:
    schema_version = 1

    def __init__(self, adapter_id: str, events: list[str], terminate: bool = False) -> None:
        self.adapter_id = adapter_id
        self.events = events
        self.terminate = terminate

    def run(self, context: object) -> dict:
        from hw_gate.cli import TerminationRequested

        self.events.append("adapter:" + self.adapter_id)
        if self.terminate:
            raise TerminationRequested(signal.SIGTERM)
        return _gate(self.adapter_id)


def _patch_measure_runtime(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    recovery_error: Exception | None = None,
    identity_valid: bool = True,
    terminate_adapter: str | None = None,
) -> list[str]:
    from hw_gate import cli

    states: list[str] = []

    def recover(manager: object) -> bool:
        events.append("recover")
        if recovery_error is not None:
            raise recovery_error
        return True

    def diagnostics(ssh: object, raw_dir: Path, process_names: tuple[str, ...]) -> list[dict]:
        events.append("diagnostics")
        return [{"id": "dmesg", "output": "bounded"}]

    real_atomic = cli.atomic_write_json

    def recording_atomic(path: Path, payload: dict, *, max_bytes: int = cli.MAX_EVIDENCE_BYTES) -> None:
        if path.name == FULL_SHA + ".json":
            states.append(payload["lifecycle"]["state"])
        real_atomic(path, payload, max_bytes=max_bytes)

    monkeypatch.setattr(cli, "SshClient", lambda host: _FakeSsh(events))
    monkeypatch.setattr(cli, "SetupManager", lambda ssh: object())
    monkeypatch.setattr(cli, "recover_pending_transaction", recover)
    monkeypatch.setattr(cli, "TargetIdentityCheck", lambda: _FakeIdentity(events, identity_valid))
    monkeypatch.setattr(cli, "collect_diagnostics", diagnostics)
    monkeypatch.setattr(cli, "atomic_write_json", recording_atomic)
    monkeypatch.setattr(cli, "ADAPTER_FACTORIES", {
        "bps_quick": lambda: _FakeAdapter("bps_quick", events, terminate_adapter == "bps_quick"),
        "mixed_combo": lambda: _FakeAdapter("mixed_combo", events, terminate_adapter == "mixed_combo"),
    })
    return states


def test_measure_orders_recovery_identity_adapters_diagnostics_and_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation may start only after recovery and identity, with teardown evidence before return."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    events: list[str] = []
    states = _patch_measure_runtime(monkeypatch, events)

    assert main([
        "measure", "--envelope", str(envelope), "--target-host", "192.168.0.5",
        "--output-dir", str(output_dir), "--gates", "mixed_combo,bps_quick",
    ]) == 2

    assert events == [
        "ssh-open", "recover", "identity", "adapter:bps_quick", "adapter:mixed_combo",
        "diagnostics", "ssh-close",
    ]
    assert states == [
        "baseline_validated", "recovery_complete", "identity_verified",
        "adapter:bps_quick", "adapter:mixed_combo", "diagnostics_complete", "complete",
    ]
    document = json.loads((output_dir / (FULL_SHA + ".json")).read_text(encoding="utf-8"))
    assert [gate["id"] for gate in document["gates"]] == ["bps_quick", "mixed_combo"]
    assert document["board"]["identity"][0]["actual"] == "measured<id>"
    assert document["source_commit"] == SOURCE_SHA
    assert document["baseline"]["source_commit"] == BASELINE_SOURCE_SHA
    assert document["deployment"] == {"mode": "predeployed", "verified": False, "artifacts": []}


def test_recovery_error_checkpoints_numeric_error_and_stops_before_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dirty-journal recovery failure must leave durable evidence without probing further."""
    from hw_gate.cli import main
    from hw_gate.transaction import TransactionError

    envelope, output_dir = _prepare(tmp_path)
    events: list[str] = []
    _patch_measure_runtime(monkeypatch, events, recovery_error=TransactionError("dirty journal"))

    assert main([
        "measure", "--envelope", str(envelope), "--target-host", "192.168.0.5",
        "--output-dir", str(output_dir),
    ]) == 2

    assert events == ["ssh-open", "recover", "diagnostics", "ssh-close"]
    document = json.loads((output_dir / (FULL_SHA + ".json")).read_text(encoding="utf-8"))
    metric = document["gates"][0]["metrics"][0]
    assert isinstance(metric["value"], int) and not isinstance(metric["value"], bool)
    assert document["gates"][0]["errors"][0]["code"] == "infrastructure.recovery"
    raw_ref = output_dir / document["gates"][0]["raw_output"]["path"]
    assert raw_ref.is_file()
    import hashlib

    assert hashlib.sha256(raw_ref.read_bytes()).hexdigest() == document["gates"][0]["raw_output"]["sha256"]


def test_ssh_open_failure_finishes_numeric_error_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A target connection failure must not escape or leave only an in-progress document."""
    from hw_gate.cli import main
    from ssh import SshConnectionError

    envelope, output_dir = _prepare(tmp_path)
    monkeypatch.setattr(
        "hw_gate.cli.SshClient",
        lambda host: (_ for _ in ()).throw(SshConnectionError("connection refused")),
    )

    assert main([
        "measure", "--envelope", str(envelope), "--target-host", "192.168.0.5",
        "--output-dir", str(output_dir),
    ]) == 2
    document = json.loads((output_dir / (FULL_SHA + ".json")).read_text(encoding="utf-8"))
    assert document["lifecycle"]["state"] == "complete"
    assert document["gates"][0]["errors"][0]["code"] == "infrastructure.ssh"


def test_identity_error_checkpoints_numeric_error_and_stops_before_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity mismatch must never permit a hardware adapter mutation."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    events: list[str] = []
    _patch_measure_runtime(monkeypatch, events, identity_valid=False)

    assert main([
        "measure", "--envelope", str(envelope), "--target-host", "192.168.0.5",
        "--output-dir", str(output_dir),
    ]) == 2

    assert events == ["ssh-open", "recover", "identity", "diagnostics", "ssh-close"]
    document = json.loads((output_dir / (FULL_SHA + ".json")).read_text(encoding="utf-8"))
    assert document["gates"][0]["errors"][0]["code"] == "infrastructure.identity"


def test_baseline_digest_and_source_binding_fail_before_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed baseline or mismatched trusted source commit must stop before target access."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    payload = json.loads(envelope.read_text(encoding="utf-8"))
    payload["baseline"]["sha256"] = "f" * 64
    envelope.write_text(json.dumps(payload), encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr("hw_gate.cli.SshClient", lambda host: opened.append(host))

    assert main([
        "measure", "--envelope", str(envelope), "--target-host", "192.168.0.5",
        "--output-dir", str(output_dir),
    ]) == 2
    assert opened == []


def test_internal_termination_becomes_error_after_diagnostics_and_ssh_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TERM/HUP unwinding must remain an ERROR path after adapter transaction teardown."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    events: list[str] = []
    _patch_measure_runtime(monkeypatch, events, terminate_adapter="bps_quick")

    assert main([
        "measure", "--envelope", str(envelope), "--target-host", "192.168.0.5",
        "--output-dir", str(output_dir),
    ]) == 2
    assert events[-2:] == ["diagnostics", "ssh-close"]
    document = json.loads((output_dir / (FULL_SHA + ".json")).read_text(encoding="utf-8"))
    assert document["verdict"] == "ERROR"
    assert document["gates"][0]["errors"][0]["code"] == "infrastructure.signal"


def test_signal_handlers_install_only_term_and_hup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator must raise one internal exception and never claim to handle SIGKILL."""
    from hw_gate.cli import TerminationRequested, installed_termination_handlers

    installed: dict[signal.Signals, object] = {}
    monkeypatch.setattr(signal, "getsignal", lambda signum: "old-" + str(signum))
    monkeypatch.setattr(signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))

    with installed_termination_handlers():
        assert set(installed) == {signal.SIGTERM, signal.SIGHUP}
        with pytest.raises(TerminationRequested) as exc_info:
            installed[signal.SIGTERM](signal.SIGTERM, None)  # type: ignore[operator]
        assert exc_info.value.signum == signal.SIGTERM

    assert signal.SIGKILL not in installed


def test_finalize_preserves_valid_child_artifact_and_recomputes_its_exit(
    tmp_path: Path,
) -> None:
    """Wrapper status must not replace valid evidence or establish its verdict."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    baseline = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
    child = json.loads((FIXTURES / "evidence_pass.json").read_text(encoding="utf-8"))
    child["run"] = json.loads(envelope.read_text(encoding="utf-8"))["run"]
    child["baseline"] = json.loads(envelope.read_text(encoding="utf-8"))["baseline"]
    child["source_commit"] = SOURCE_SHA
    child["comparability"] = baseline["comparability"]
    result = output_dir / (FULL_SHA + ".json")
    original = (json.dumps(child, sort_keys=True, separators=(",", ":")) + "\n").encode()
    result.write_bytes(original)

    assert main([
        "finalize", "--envelope", str(envelope), "--output-dir", str(output_dir),
        "--child-exit-code", "73",
    ]) == 2
    assert result.read_bytes() == original


def test_finalize_emits_busy_only_for_exit_4_without_child_artifact(tmp_path: Path) -> None:
    """Lease contention is the sole canonical zero-gate terminal document."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    assert main([
        "finalize", "--envelope", str(envelope), "--output-dir", str(output_dir),
        "--child-exit-code", "4",
    ]) == 4

    document = json.loads((output_dir / (FULL_SHA + ".json")).read_text(encoding="utf-8"))
    assert document["verdict"] == "BUSY"
    assert document["board"]["lease_exit_code"] == 4
    assert document["gates"] == []


@pytest.mark.parametrize(("child_exit", "verdict"), [(4, "BUSY"), (2, "ERROR")])
def test_finalize_survives_missing_baseline_after_trusted_prepare(
    tmp_path: Path, child_exit: int, verdict: str,
) -> None:
    """Finalization must keep always-upload true when local baseline preflight fails."""
    from hw_gate.cli import main

    baseline = tmp_path / "baseline.json"
    baseline.write_bytes((FIXTURES / "baseline.json").read_bytes())
    output_dir = tmp_path / "hw-results"
    args = _prepare_args(tmp_path, baseline=str(baseline))
    assert main(args) == 0
    envelope = output_dir / (FULL_SHA + ".candidate.json")
    baseline.unlink()

    assert main([
        "finalize", "--envelope", str(envelope), "--output-dir", str(output_dir),
        "--child-exit-code", str(child_exit),
    ]) == child_exit
    document = json.loads((output_dir / (FULL_SHA + ".json")).read_text(encoding="utf-8"))
    assert document["verdict"] == verdict
    assert len(document["gates"]) == (0 if verdict == "BUSY" else 1)


@pytest.mark.parametrize("child_exit", [0, 1, 2, 137])
def test_finalize_missing_child_is_one_numeric_infrastructure_error(
    tmp_path: Path, child_exit: int,
) -> None:
    """No wrapper status, including zero, may synthesize hardware PASS."""
    from hw_gate.cli import main

    envelope, output_dir = _prepare(tmp_path)
    assert main([
        "finalize", "--envelope", str(envelope), "--output-dir", str(output_dir),
        "--child-exit-code", str(child_exit),
    ]) == 2

    document = json.loads((output_dir / (FULL_SHA + ".json")).read_text(encoding="utf-8"))
    assert document["verdict"] == "ERROR"
    assert len(document["gates"]) == 1
    gate = document["gates"][0]
    assert gate["id"] == "infrastructure"
    assert gate["metrics"][0]["id"] == "infrastructure.child_exit_code"
    assert gate["metrics"][0]["value"] == child_exit
    assert gate["errors"][0]["code"] == "infrastructure.child_exit"


def test_atomic_json_replaces_from_same_directory_mode_600_and_enforces_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkpoint must never expose a partial or oversized evidence file."""
    from hw_gate import cli

    target = tmp_path / "evidence.json"
    replacements: list[tuple[Path, Path]] = []
    original_replace = Path.replace

    def recording_replace(source: Path, destination: Path) -> Path:
        replacements.append((source, destination))
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", recording_replace)
    cli.atomic_write_json(target, {"safe": "value"})
    assert replacements and replacements[0][0].parent == target.parent
    assert target.stat().st_mode & 0o777 == 0o600

    with pytest.raises(EvidenceError, match="1,048,576"):
        cli.atomic_write_json(target, {"large": "x" * 1_048_576})


def test_diagnostics_are_allowlisted_and_hard_bounded(tmp_path: Path) -> None:
    """Failure collection must not capture credentials, arbitrary processes, or full edgeconf."""
    from hw_gate.diagnostics import collect_diagnostics

    class DiagnosticSsh:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def run(self, command: str) -> str:
            self.commands.append(command)
            if command.startswith("dmesg"):
                return "\n".join("line-{0}".format(index) for index in range(500))
            return "secret<value>|" * 5000

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "gate.json").write_bytes(b"x" * 20_000)
    ssh = DiagnosticSsh()

    diagnostics = collect_diagnostics(ssh, raw_dir, ("gstApp", "pim-service"))

    assert ssh.commands[0] == "dmesg --color=never | tail -n 200"
    assert "edgeconf_pim.json" in ssh.commands[1]
    assert "cat " not in ssh.commands[1]
    assert "password" not in " ".join(ssh.commands).lower()
    assert "gstApp" in ssh.commands[2] and "pim-service" in ssh.commands[2]
    assert all(token in ssh.commands[3] for token in ("modinfo", "sha256sum", "modprobe --dump-modversions"))
    assert all("sshd" not in command for command in ssh.commands)
    dmesg = next(item for item in diagnostics if item["id"] == "dmesg")
    assert len(dmesg["output"].splitlines()) <= 200
    tail = next(item for item in diagnostics if item["id"] == "raw:gate.json")
    assert len(tail["output"].encode("utf-8")) <= 16_384
    module = next(item for item in diagnostics if item["id"] == "module.max9296")
    assert len(module["output"].encode("utf-8")) <= 16_384


def test_diagnostics_reject_undeclared_process_names(tmp_path: Path) -> None:
    """Process filtering must not become a shell-command injection surface."""
    from hw_gate.diagnostics import collect_diagnostics

    class NeverSsh:
        def run(self, command: str) -> str:
            raise AssertionError("unsafe process name reached SSH")

    with pytest.raises(EvidenceError, match="process name"):
        collect_diagnostics(NeverSsh(), tmp_path, ("pim; id",))


def test_markdown_is_deterministic_complete_and_escapes_measured_controls() -> None:
    """Publisher-facing text must be complete without allowing measured Markdown or HTML."""
    from hw_gate.render import render_markdown

    document = json.loads((FIXTURES / "evidence_pass.json").read_text(encoding="utf-8"))
    document.update({
        "run": {
            "repository": "jhw7500/pim-check",
            "pr_head_sha": FULL_SHA,
            "workflow_run_id": 123,
            "workflow_run_attempt": 2,
            "run_url": "https://github.com/jhw7500/pim-check/actions/runs/123/attempts/2",
        },
        "baseline": {"sha256": "b" * 64, "source_commit": SOURCE_SHA, "path": "baselines/hw-baseline.json"},
        "board": {"id": "pim", "target_host": "192.168.0.5", "identity": [{"id": "<driver>|*x*", "actual": "a[b]`c`"}]},
        "diagnostics": [{"id": "dmesg", "output": "<bad>|**bold**"}],
    })
    gate = document["gates"][0]
    gate["preconditions"][0]["id"] = "<pre>|x"
    gate["restoration"] = {"verdict": "PASS", "before_sha256": "<before>", "after_sha256": "after|sha"}

    first = render_markdown(document)
    second = render_markdown(copy.deepcopy(document))

    assert first == second
    for required in (
        "predeployed measurement", FULL_SHA, "b" * 64, "https://github.com/jhw7500/pim-check/actions/runs/123/attempts/2",
        "Target identities", "Metrics", "Rule", "Delta", "Preconditions", "Restoration", "Diagnostics",
    ):
        assert required in first
    for unsafe in ("<driver>", "<bad>", "**bold**", "|*x*", "a[b]`c`"):
        assert unsafe not in first


def test_validate_recomputes_and_rejects_declared_pass_disagreement(tmp_path: Path) -> None:
    """The validation command must use baseline rules, not the producer verdict or exit code."""
    from hw_gate.cli import main

    evidence = json.loads((FIXTURES / "evidence_pass.json").read_text(encoding="utf-8"))
    evidence["verdict"] = "PASS"
    evidence["overall_verdict"] = "PASS"
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    assert main(["validate", "--evidence", str(path), "--baseline", str(FIXTURES / "baseline.json")]) == 2


def test_module_help_lists_the_four_commands(capsys: pytest.CaptureFixture[str]) -> None:
    """The public module surface must expose exactly the phase-one lifecycle."""
    from hw_gate.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    for command in ("prepare", "measure", "finalize", "validate"):
        assert command in output
