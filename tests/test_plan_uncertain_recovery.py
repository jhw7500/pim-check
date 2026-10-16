"""An uncertain camera13 recovery must not trigger a competing plan action."""
from unittest.mock import MagicMock

import pytest

from plan import DEFAULT_EXECUTION, DEFAULT_GATE, Plan, execute_plan
from setup import EDGECONF_PATH, SetupManager


def test_plan_does_not_retry_continue_or_teardown_ambiguous_recovery(tmp_path):
    (tmp_path / "cases").mkdir()
    (tmp_path / "base.yaml").write_text(
        "target:\n  host: 192.168.0.5\n  user: root\n"
        "monitor:\n  duration_sec: 0\n  interval_sec: 5\n")
    (tmp_path / "cases" / "first.yaml").write_text(
        "name: first\nsetup:\n  edgeconf_changes:\n"
        "    .VHL_CAM.cam_width: 1920\n  reboot_after: true\n")
    (tmp_path / "cases" / "second.yaml").write_text(
        "name: second\nsetup:\n  edgeconf_changes:\n"
        "    .VHL_CAM.cam_width: 1280\n  reboot_after: true\n")
    plan = Plan(
        name="safe", description="safe", version=1,
        cases={"regression": ["first", "second"]},
        execution={**DEFAULT_EXECUTION, "reset_mode": "camera_hard",
                   "case_retry": 2, "stop_on_fail": False},
        gate=dict(DEFAULT_GATE), reports=[],
    )
    instances = []
    engine_calls = []

    def ssh_factory(*_args):
        ssh = MagicMock()
        ssh.check_connectivity.return_value = True
        return ssh

    def setup_factory(ssh):
        mgr = MagicMock()
        mgr._config_snapshots = {EDGECONF_PATH: "baseline"}
        mgr._snapshot_failures = set()
        mgr._recovery_outcome_uncertain = False

        def fail_setup(_cfg, **_kw):
            mgr._recovery_outcome_uncertain = True
            raise RuntimeError("camera13 result cannot be verified")

        mgr.run_setup.side_effect = fail_setup
        instances.append(mgr)
        return mgr

    def engine_factory(_ssh, profile):
        engine_calls.append(profile)
        return MagicMock()

    results = execute_plan(
        plan, str(tmp_path), ssh_factory=ssh_factory,
        setup_factory=setup_factory, engine_factory=engine_factory,
    )
    assert len(results) == 1
    assert results[0].case_name == "first"
    assert results[0].passed is False
    assert "SETUP_EXCEPTION" in (results[0].error or "")
    assert len(instances) == 1
    instances[0].run_setup.assert_called_once()
    instances[0].run_teardown.assert_not_called()
    assert engine_calls == []


def test_setup_manager_blocks_cleanup_after_request_timeout():
    ssh = MagicMock()
    ssh.run.side_effect = [
        "2026-10-15 13:00:00\nboot-id",
        "PIM_RECOVERY_CLI_RC=124",
    ]
    mgr = SetupManager(ssh)
    with pytest.raises(RuntimeError, match="no competing reboot"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=False)
    assert mgr._recovery_outcome_uncertain is True
    with pytest.raises(RuntimeError, match="cleanup deferred"):
        mgr.run_teardown({
            "edgeconf_changes": {".VHL_CAM.cam_width": 1920},
            "reboot_after": True, "reset_mode": "camera_hard",
        })
    commands = [call.args[0] for call in ssh.run.call_args_list]
    assert sum("cam-recoveryctl" in command for command in commands) == 1
    assert "reboot" not in commands
