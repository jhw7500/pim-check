"""CLI·병렬 실행의 setup 예외도 변경 시도 경계와 복구 미확인을 유지한다.

외부 보드 조작은 금지한다. run_setup/run_teardown만 실제 SetupManager 코드로
실행하고 SSH, 복원 대상, 재부팅은 mock으로 대체한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from setup import EDGECONF_PATH, SetupManager


def _ssh():
    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    ssh.preflight_check.return_value = []
    return ssh


def _run(entrypoint, profile, ssh, manager):
    with patch(f"{entrypoint}.load_profile", return_value=profile), \
         patch(f"{entrypoint}.SshClient", return_value=ssh), \
         patch(f"{entrypoint}.SetupManager", return_value=manager):
        if entrypoint == "pim_check":
            import pim_check
            outcome = pim_check.run_case("case", "mock-board", "root", "root", 0, quiet=True)
        else:
            from parallel import run_on_target
            outcome = run_on_target("mock-board", "root", "root", "case", 0)
    return outcome


@pytest.mark.parametrize("entrypoint", ["pim_check", "parallel"])
def test_already_matching_hard_reset_rejection_does_not_recover(entrypoint):
    ssh = _ssh()
    mgr = SetupManager(ssh)
    mgr.check_current = MagicMock(return_value=True)
    mgr._local0_log = MagicMock()
    mgr.run_teardown = MagicMock(wraps=mgr.run_teardown)
    mgr.reset_and_wait = MagicMock()
    profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
               "setup": {"edgeconf_changes": {".VHL_CAM.fps": 30},
                         "reset_mode": "camera_hard",
                         "require_hard_reset": True, "reboot_after": True},
               "teardown": {"recovery_command": "recover"}}
    result = _run(entrypoint, profile, ssh, mgr)
    if entrypoint == "pim_check":
        assert result == 1
    else:
        assert result["status"] == "SETUP_FAILED"
    mgr.run_teardown.assert_not_called()
    mgr.reset_and_wait.assert_not_called()
    ssh.run.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["pim_check", "parallel"])
def test_partial_config_application_recovers_without_recovery_command(entrypoint):
    ssh = _ssh()
    mgr = SetupManager(ssh)
    mgr._local0_log = MagicMock()
    mgr.check_current = MagicMock(return_value=False)
    mgr._snapshot_or_warn = MagicMock()
    mgr.backup = MagicMock(return_value=True)
    mgr.apply_changes = MagicMock(side_effect=RuntimeError("config may be partial"))
    mgr._restore_conf = MagicMock()
    mgr.reset_and_wait = MagicMock()
    mgr.run_teardown = MagicMock(wraps=mgr.run_teardown)
    setup = {"edgeconf_changes": {".VHL_CAM.fps": 30}, "reboot_after": True}
    profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {}, "setup": setup}
    result = _run(entrypoint, profile, ssh, mgr)
    if entrypoint == "pim_check":
        assert result == 1
    else:
        assert result["status"] == "SETUP_FAILED"
    mgr.run_teardown.assert_called_once_with(setup, {})
    mgr._restore_conf.assert_called_once_with(EDGECONF_PATH)
    mgr.reset_and_wait.assert_called_once()


@pytest.mark.parametrize("entrypoint", ["pim_check", "parallel"])
def test_uncertain_camera_result_never_requests_competing_cleanup(entrypoint):
    ssh = _ssh()
    mgr = SetupManager(ssh)
    mgr._recovery_outcome_uncertain = True
    mgr.run_setup = MagicMock(side_effect=RuntimeError("camera recovery result unknown"))
    mgr.run_teardown = MagicMock(wraps=mgr.run_teardown)
    profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
               "setup": {"inject_command": "inject"},
               "teardown": {"recovery_command": "recover"}}
    result = _run(entrypoint, profile, ssh, mgr)
    if entrypoint == "pim_check":
        assert result == 4
    else:
        assert result["status"] == "ERROR"
        assert result["recovery_unverified"] is True
        assert "FAULT_RECOVERY_UNVERIFIED" in result["error"]
    mgr.run_teardown.assert_not_called()
    ssh.run.assert_not_called()
