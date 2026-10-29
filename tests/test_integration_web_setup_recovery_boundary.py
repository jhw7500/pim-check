"""Web setup 예외가 복원 전/후 상태와 불확실한 카메라 요청을 혼동하지 않는다.

실보드 접근 없이 원래 SetupManager 제어 흐름과 mock SSH를 함께 검증한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import web
from setup import EDGECONF_PATH, SetupManager


def _ssh():
    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    ssh.preflight_check.return_value = []
    return ssh


def _run(profile, ssh, mgr):
    with patch("web.load_profile", return_value=profile), \
         patch("web.SshClient", return_value=ssh), \
         patch("web.SetupManager", return_value=mgr), \
         patch("web.Engine") as engine_cls:
        result = web._run_test("case", "mock-board", duration=0)
    engine_cls.assert_not_called()
    assert web._run_state["active"] is False
    return result


def test_web_already_matching_hard_reset_rejection_is_non_mutating():
    ssh = _ssh()
    mgr = SetupManager(ssh)
    mgr.check_current = MagicMock(return_value=True)
    mgr._local0_log = MagicMock()
    mgr.reset_and_wait = MagicMock()
    mgr.run_teardown = MagicMock(wraps=mgr.run_teardown)
    profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
               "setup": {"edgeconf_changes": {".VHL_CAM.fps": 30},
                         "reset_mode": "camera_hard",
                         "require_hard_reset": True, "reboot_after": True},
               "teardown": {"recovery_command": "recover"}}
    result = _run(profile, ssh, mgr)
    assert result["status"] == "ERROR"
    mgr.run_teardown.assert_not_called()
    mgr.reset_and_wait.assert_not_called()
    ssh.run.assert_not_called()


def test_web_partial_apply_failure_restores_even_without_recovery_command():
    ssh = _ssh()
    mgr = SetupManager(ssh)
    mgr._local0_log = MagicMock()
    mgr.check_current = MagicMock(return_value=False)
    mgr._snapshot_or_warn = MagicMock()
    mgr.backup = MagicMock(return_value=True)
    mgr.apply_changes = MagicMock(side_effect=RuntimeError("partial configuration write"))
    mgr._restore_conf = MagicMock()
    mgr.reset_and_wait = MagicMock()
    mgr.run_teardown = MagicMock(wraps=mgr.run_teardown)
    setup = {"edgeconf_changes": {".VHL_CAM.fps": 30}, "reboot_after": True}
    profile = {"target": {}, "monitor": {"duration_sec": 0},
               "checks": {}, "setup": setup}
    result = _run(profile, ssh, mgr)
    assert result["status"] == "ERROR"
    mgr.run_teardown.assert_called_once_with(setup, {})
    mgr._restore_conf.assert_called_once_with(EDGECONF_PATH)
    mgr.reset_and_wait.assert_called_once()


def test_web_unknown_camera_recovery_does_not_submit_competing_cleanup():
    ssh = _ssh()
    mgr = SetupManager(ssh)
    mgr._recovery_outcome_uncertain = True
    mgr.run_setup = MagicMock(side_effect=RuntimeError("camera13 request result unknown"))
    mgr.run_teardown = MagicMock(wraps=mgr.run_teardown)
    profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
               "setup": {"inject_command": "inject"},
               "teardown": {"recovery_command": "recover"}}
    result = _run(profile, ssh, mgr)
    assert result["status"] == "ERROR"
    assert result["recovery_unverified"] is True
    assert "FAULT_RECOVERY_UNVERIFIED" in result["message"]
    mgr.run_teardown.assert_not_called()
    ssh.run.assert_not_called()
