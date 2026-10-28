"""Fail-closed fault cleanup and ORD/VCM-independent recovery postconditions.

The target is mocked: never obtain a board lease or inject physical faults here.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import load_profile
from plan import DEFAULT_EXECUTION, DEFAULT_GATE, Plan, execute_plan
from setup import SetupManager

PROFILES = str(Path(__file__).resolve().parents[1] / "profiles")


@pytest.mark.parametrize("observed,success", [
    ("OK", True), (" OK\n", True), ("FAIL", False), (None, False),
    ("remount FAILED", False), ("OK\nFAIL", False),
])
def test_teardown_recovery_requires_independent_postcondition(observed, success):
    seen = []
    ssh = MagicMock()

    def remote(cmd, **kwargs):
        seen.append(cmd)
        if cmd == "check-postcondition":
            return observed
        return "done"

    ssh.run.side_effect = remote
    mgr = SetupManager(ssh)
    mgr._local0_log = MagicMock()
    cfg = {"recovery_command": "clear-injected-fault",
           "recovery_verify_command": "check-postcondition"}
    if success:
        mgr.run_teardown({}, cfg)
    else:
        with pytest.raises(RuntimeError, match="fault recovery postcondition failed"):
            mgr.run_teardown({}, cfg)
    assert seen == ["clear-injected-fault", "check-postcondition"]


def test_teardown_verification_without_cleanup_command_is_invalid():
    mgr = SetupManager(MagicMock())
    mgr._local0_log = MagicMock()
    with pytest.raises(RuntimeError, match="requires recovery_command"):
        mgr.run_teardown({}, {"recovery_verify_command": "check-postcondition"})


def test_production_fault_cases_pin_post_cleanup_checks():
    for case in ("fault_gstapp_crash", "fault_sd_unmounted"):
        profile = load_profile(PROFILES, case=case)
        teardown = profile["teardown"]
        assert teardown["recovery_command"]
        assert "recovery_verify_command" in teardown
        assert "echo OK" in teardown["recovery_verify_command"]

    sd = load_profile(PROFILES, case="fault_sd_unmounted")
    remount = next(s for s in sd["checks"]["custom_commands"]
                   if s["name"] == "SD auto-remount within 60s")
    assert remount["expected_regex"] == r"^REMOUNTED:[0-9]+s$"


def _campaign(tmp_path, *, recovery_ok: bool):
    (tmp_path / "cases").mkdir()
    (tmp_path / "base.yaml").write_text(
        "target:\n  host: 192.168.0.5\n  user: root\n  password: root\n"
        "monitor:\n  duration_sec: 0\n  interval_sec: 5\n")
    for tag in "AB":
        (tmp_path / "cases" / f"fault_{tag}.yaml").write_text(
            f"name: fault_{tag}\n"
            f"setup:\n  inject_command: inject-{tag}\n"
            f"teardown:\n  recovery_command: recover-{tag}\n"
            f"  recovery_verify_command: check-{tag}\n")
    plan = Plan(
        name="fault cleanup", description="postcondition audit", version=1,
        cases={"regression": ["fault_A", "fault_B"]},
        execution={**DEFAULT_EXECUTION, "case_retry": 1,
                   "retry_wait_sec": 0, "stop_on_fail": False},
        gate=dict(DEFAULT_GATE), reports=[],
    )
    sent = []
    managers = []

    def ssh_factory(*args):
        ssh = MagicMock()
        ssh.check_connectivity.return_value = True

        def run(command, **kwargs):
            sent.append(command)
            if command == "check-A" and not recovery_ok:
                return "FAIL"
            if command.startswith("check-"):
                return "OK"
            return "done"

        ssh.run.side_effect = run
        return ssh

    def setup_factory(ssh):
        mgr = SetupManager(ssh)
        mgr._local0_log = MagicMock()
        mgr.reset_and_wait = MagicMock()
        managers.append(mgr)
        return mgr

    def engine_factory(ssh, profile):
        engine = MagicMock()
        engine.run_snapshot.return_value = [
            {"name": "d", "passed": True, "reason": "OK",
             "data": {}, "duration_ms": 1}
        ]
        return engine

    result = execute_plan(
        plan, str(tmp_path), ssh_factory=ssh_factory,
        setup_factory=setup_factory, engine_factory=engine_factory)
    return result, sent, managers


def test_failed_recovery_marks_case_fail_and_stops_even_without_stop_on_fail(tmp_path):
    result, sent, managers = _campaign(tmp_path, recovery_ok=False)
    assert len(result) == 1, "The next fault was injected after failed recovery"
    assert result[0].case_name == "fault_A"
    assert result[0].passed is False
    assert result[0].error is not None
    assert "FAULT_RECOVERY_UNVERIFIED" in result[0].error
    assert "inject-A" in sent
    assert "recover-A" in sent
    assert "check-A" in sent
    assert "inject-B" not in sent
    assert all(not mgr.reset_and_wait.called for mgr in managers)


def test_verified_recovery_completes_before_next_fault_injection(tmp_path):
    result, sent, managers = _campaign(tmp_path, recovery_ok=True)
    assert len(result) == 2
    assert all(case.passed for case in result)
    for tag in "AB":
        assert sent.index(f"inject-{tag}") < sent.index(f"recover-{tag}")
        assert sent.index(f"recover-{tag}") < sent.index(f"check-{tag}")
    assert sent.index("check-A") < sent.index("inject-B")
    assert all(not mgr.reset_and_wait.called for mgr in managers)


def test_gstapp_fault_requires_proof_of_actual_pid_change():
    profile = load_profile(PROFILES, case="fault_gstapp_crash")
    inject = profile["setup"]["inject_command"]
    assert any("oldpid" in cmd and "pkill -9 gstApp" in cmd for cmd in inject)
    spec = next(item for item in profile["checks"]["custom_commands"]
                if item["name"] == "gstApp PID changed after injected crash")
    check = __import__("checks.custom", fromlist=["CustomCommandCheck"]).CustomCommandCheck()
    ssh = MagicMock()
    ssh.run.return_value = "INJECT_NOT_CONFIRMED"
    data = check.collect(ssh, {"custom_commands": [spec]})
    assert not check.validate(data, {})[0]
    ssh.run.return_value = "INJECT_CONFIRMED"
    assert check.validate(check.collect(ssh, {"custom_commands": [spec]}), {})[0]
