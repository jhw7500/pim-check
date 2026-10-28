"""Fail-closed contract for the smoke case that intentionally kills 3 processes.

No board/SSH access: the injected commands are intercepted by mocked SshClient,
and shell -n only parses the YAML commands without executing them.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from checks.custom import CustomCommandCheck, item_results
from config import load_profile
from plan import DEFAULT_EXECUTION, DEFAULT_GATE, Plan, execute_plan

PROFILE_ROOT = str(Path(__file__).resolve().parents[1] / "profiles")
CASE = "process_restart_smoke"


def profile():
    return load_profile(PROFILE_ROOT, case=CASE)


def test_process_restart_injection_is_setup_only_and_single_attempt():
    case = profile()
    assert case["setup"]["single_injection"] is True
    assert case["monitor"]["duration_sec"] == 0
    inject = case["setup"]["inject_command"]
    assert inject.count("pkill -9 -x") == 3
    for name in ("gstApp", "ord", "vcm"):
        assert f"pkill -9 -x {name}" in inject
        assert f"pgrep -x {name}" in inject
    assert "STALE_MARKER" in inject and "PRECONDITION_FAILED" in inject
    assert "AUTO_RECOVERY_FAILED" in inject and "INJECT_VERIFIED" in inject
    assert "pim_restart_smoke_before" in inject
    assert "pim_restart_smoke_state" in inject
    checks = case["checks"]["custom_commands"]
    assert len(checks) == 5  # existing quick_verify inventory contract
    assert all("pkill" not in item["command"] for item in checks)
    assert all("systemctl" not in item["command"] for item in checks)
    assert all("cam-recoveryctl" not in item["command"] for item in checks)
    assert all("reboot" not in item["command"].lower() for item in checks)


@pytest.mark.parametrize("observed,expected", [
    ("INJECT_VERIFIED", True),
    ("PRECONDITION_FAILED", False),
    ("STALE_MARKER", False),
    ("AUTO_RECOVERY_FAILED", False),
    ("NO_INJECTION_STATE", False),
    ("", False),
])
def test_injection_marker_must_prove_all_three_pid_transitions(observed, expected):
    spec = profile()["checks"]["custom_commands"][0]
    assert spec["expected"] == "INJECT_VERIFIED"
    ssh = MagicMock()
    ssh.run.return_value = observed
    check = CustomCommandCheck()
    collected = check.collect(ssh, {"custom_commands": [spec]})
    assert check.validate(collected, {})[0] is expected
    assert item_results(collected)[0]["passed"] is expected


def test_recovery_attempt_uses_only_deployed_gstapp_api_then_watchdog():
    t = profile()["teardown"]
    command = t["recovery_command"]
    verify = t["recovery_verify_command"]
    assert "cam-recoveryctl request gstapp_restart" in command
    assert "systemctl restart" not in command
    assert "reboot" not in command
    assert "pkill" not in command
    assert "gstApp" in verify and "ord" in verify and "vcm" in verify
    assert "cam_state/state" in verify and "STALE_MARKER" in verify
    assert "ord_vcm_conf.json" in verify
    assert "pim_restart_smoke_before" in verify
    assert "echo FAIL" in verify and "echo OK" in verify


def test_smoke_shell_syntax_without_executing_any_command():
    case = profile()
    cmds = [case["setup"]["inject_command"],
            case["teardown"]["recovery_command"],
            case["teardown"]["recovery_verify_command"]]
    cmds += [item["command"] for item in case["checks"]["custom_commands"]]
    for cmd in cmds:
        parsed = subprocess.run(["/bin/sh", "-n", "-c", cmd], capture_output=True, text=True)
        assert parsed.returncode == 0, (cmd, parsed.stderr)


def run_mocked_campaign(*, recovery_observation, first_case_passes):
    calls = []
    inject = profile()["setup"]["inject_command"]
    cleanup = profile()["teardown"]["recovery_command"]
    verify = profile()["teardown"]["recovery_verify_command"]
    names = []

    def ssh_factory(*args):
        ssh = MagicMock()
        ssh.check_connectivity.return_value = True

        def remote(cmd, **kwargs):
            calls.append(cmd)
            if cmd == verify:
                return recovery_observation
            if cmd == cleanup:
                return "RECOVERY_ATTEMPTED"
            if cmd == inject:
                return "INJECT_VERIFIED"
            return "OK"

        ssh.run.side_effect = remote
        return ssh

    def engine_factory(ssh, cfg):
        names.append(cfg.get("name"))
        return MagicMock()

    def verify_stub(engine, ssh, duration, **kwargs):
        is_first = len(names) == 1
        passed = first_case_passes if is_first else True
        rows = [{"name": "mock", "passed": passed,
                 "reason": "UNEXPECTED" if not passed else "OK",
                 "data": {}, "duration_ms": 1}]
        return (rows, 1, 1)

    smoke = Plan(
        name="two cases", description="mocked only", version=1,
        cases={"regression": [CASE, "board_hw_check"]},
        execution={**DEFAULT_EXECUTION, "case_retry": 1,
                   "retry_wait_sec": 0, "stop_on_fail": False},
        gate=dict(DEFAULT_GATE), reports=[],
    )
    with patch("verify_retry.run_verify_with_retry", side_effect=verify_stub):
        result = execute_plan(smoke, PROFILE_ROOT, ssh_factory=ssh_factory,
                              setup_factory=__import__("setup", fromlist=["SetupManager"]).SetupManager,
                              engine_factory=engine_factory)
    return result, calls, names, inject, cleanup, verify


def test_failed_cleanup_marks_fault_case_failed_and_blocks_subsequent_board_case():
    result, calls, names, inj, cleanup, verify = run_mocked_campaign(
        recovery_observation="FAIL", first_case_passes=True)
    assert len(result) == 1
    assert result[0].passed is False
    assert "FAULT_RECOVERY_UNVERIFIED" in result[0].error
    assert result[0].retries_used == 0
    assert calls.count(inj) == 1
    assert calls.count(cleanup) == 1
    assert calls.count(verify) == 1
    assert len(names) == 1
    assert not any("reboot" in cmd for cmd in calls)


def test_check_failure_does_not_reinject_even_when_plan_case_retry_is_one():
    result, calls, names, inj, cleanup, verify = run_mocked_campaign(
        recovery_observation="OK", first_case_passes=False)
    assert [r.passed for r in result] == [False, True]
    assert result[0].retries_used == 0
    assert calls.count(inj) == 1
    assert calls.count(cleanup) == 1
    assert calls.count(verify) == 1
    assert len(names) == 2


def test_successful_fault_and_cleanup_reaches_next_case_once():
    result, calls, names, inj, cleanup, verify = run_mocked_campaign(
        recovery_observation="OK", first_case_passes=True)
    assert len(result) == 2
    assert all(r.passed for r in result)
    assert calls.count(inj) == 1
    assert calls.count(verify) == 1
    assert calls.index(inj) < calls.index(cleanup) < calls.index(verify)


def test_setup_logger_does_not_expand_shell_substitutions_in_fault_commands():
    import shlex

    from setup import SetupManager

    ssh = MagicMock()
    msg = 'INJECT command $(echo BAD) `printf BAD` "$HOME"'
    mgr = SetupManager(ssh)
    mgr._local0_log(msg)
    command = ssh.run.call_args.args[0]
    assert shlex.split(command) == [
        "logger", "-p", "local0.notice", "-t", "PIM_CHECK", msg
    ]
    assert ssh.run.call_args.kwargs["retries"] == 0
