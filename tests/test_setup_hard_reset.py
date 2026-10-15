"""camera13 owner-serialized recovery contract tests.

These checks never contact the device; board acceptance requires a separate
exclusive lease. The historical 45-second deprecated wrapper is not used.
"""
from unittest.mock import MagicMock

import pytest

from setup import (
    SetupManager,
    _parse_recovery_result,
    _recovery_cli_command,
)


ANCHOR_OUTPUT = "2026-09-01 12:00:00\nboot-id"
REQUEST_ID = "01234567-89ab-cdef-0123-456789abcdef"


def terminal(kind="camera_hard_reset", *, status="SUCCEEDED", rc=0, cli_rc=0):
    return (f"CAM_RECOVERY_RESULT id={REQUEST_ID} type={kind} "
            f"status={status} rc={rc}\nPIM_RECOVERY_CLI_RC={cli_rc}")


def manager(output=None):
    ssh = MagicMock()
    ssh.run.side_effect = [ANCHOR_OUTPUT, output or terminal()]
    mgr = SetupManager(ssh)
    mgr._stabilize = MagicMock()
    mgr.reboot_and_wait = MagicMock()
    mgr._local0_log = MagicMock()
    return mgr, ssh


def _commands(ssh):
    return [call.args[0] for call in ssh.run.call_args_list]


def test_prime_same_boot_anchor_writes_wall_clock_and_boot_id():
    mgr, ssh = manager(ANCHOR_OUTPUT)
    assert mgr._prime_same_boot_anchor() is True
    assert "date '+%Y-%m-%d %H:%M:%S'" in _commands(ssh)[0]
    assert "/proc/sys/kernel/random/boot_id" in _commands(ssh)[0]
    assert "> /tmp/pim_check_anchor" in _commands(ssh)[0]


def test_explicit_hard_reset_uses_camera13_request_contract():
    mgr, ssh = manager()
    assert mgr.reset_and_wait("camera_hard", 120, ord_changed=False) == "camera_hard"
    commands = _commands(ssh)
    assert len(commands) == 2
    assert "date '+%Y-%m-%d %H:%M:%S'" in commands[0]
    assert "/opt/pim/bin/cam-recoveryctl request camera_hard_reset" in commands[1]
    assert "--source pim-check" in commands[1]
    assert "--wait 300" in commands[1]
    assert "PIM_RECOVERY_CLI_RC=%s" in commands[1]
    assert "cam_hard_reset.sh" not in commands[1]
    assert "-s -S" not in commands[1]
    assert "timeout 45" not in commands[1]
    assert ssh.run.call_args.kwargs["retries"] == 0
    mgr._stabilize.assert_called_once_with(120)
    mgr.reboot_and_wait.assert_not_called()


def test_source_changes_use_apply_config_to_recreate_runtime_before_reset():
    mgr, ssh = manager(terminal("apply_config"))
    assert mgr.reset_and_wait("camera_hard", 120, ord_changed=False,
                              source_changed=True) == "camera_hard"
    assert "/opt/pim/bin/cam-recoveryctl apply-config" in _commands(ssh)[1]
    assert "request camera_hard_reset" not in _commands(ssh)[1]
    mgr.reboot_and_wait.assert_not_called()


def test_apply_config_result_cannot_be_misreported_as_hard_reset():
    mgr, ssh = manager(terminal("camera_hard_reset"))
    with pytest.raises(RuntimeError, match="verified success"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=False,
                           source_changed=True)
    mgr.reboot_and_wait.assert_not_called()
    assert len(_commands(ssh)) == 2


@pytest.mark.parametrize("rc", [64, 69, 70, 75, 124])
def test_cli_refusal_or_wait_timeout_is_not_followed_by_reboot(rc):
    mgr, ssh = manager(f"PIM_RECOVERY_CLI_RC={rc}")
    with pytest.raises(RuntimeError, match="no competing reboot"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=False)
    assert len(_commands(ssh)) == 2
    mgr.reboot_and_wait.assert_not_called()
    mgr._stabilize.assert_not_called()
    assert mgr._preserve_session_anchor is False


@pytest.mark.parametrize("bad_output", [
    None,
    "",
    "CAM_RECOVERY_RESULT id=" + REQUEST_ID
    + " type=camera_hard_reset status=SUCCEEDED rc=0",
    "PIM_RECOVERY_CLI_RC=0",
    terminal(status="FAILED", rc=70, cli_rc=70),
    terminal(status="FAILED", rc=70, cli_rc=0),
    terminal(rc=70),
    terminal(kind="module_reload"),
    terminal().replace(REQUEST_ID, "not-a-uuid"),
    terminal().replace("status=SUCCEEDED", "status=RUNNING"),
    terminal() + "\nPIM_RECOVERY_CLI_RC=0",
    terminal() + "\nCAM_RECOVERY_RESULT id=" + REQUEST_ID
    + " type=camera_hard_reset status=SUCCEEDED rc=0",
])
def test_bad_or_ambiguous_result_fails_closed_without_extra_reset(bad_output):
    mgr, ssh = manager(bad_output)
    # Assign explicit None/empty outputs; manager() has a success default.
    ssh.run.side_effect = [ANCHOR_OUTPUT, bad_output]
    with pytest.raises(RuntimeError, match="no competing reboot"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=False)
    mgr.reboot_and_wait.assert_not_called()
    mgr._stabilize.assert_not_called()
    assert len(_commands(ssh)) == 2


def test_ssh_transport_exception_never_triggers_extra_reboot():
    mgr, ssh = manager()
    ssh.run.side_effect = [ANCHOR_OUTPUT, RuntimeError("SSH lost")]
    with pytest.raises(RuntimeError, match="no competing reboot"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=False)
    mgr.reboot_and_wait.assert_not_called()


def test_anchor_preflight_failure_reboots_before_submitting_any_request():
    mgr, ssh = manager()
    ssh.run.side_effect = ["missing boot id"]
    preserved = []
    mgr.reboot_and_wait.side_effect = lambda **_: preserved.append(
        mgr._preserve_session_anchor)
    assert mgr.reset_and_wait("camera_hard", 120, ord_changed=False) == "reboot"
    assert preserved == [False]
    assert not any("cam-recoveryctl" in command for command in _commands(ssh))


def test_ord_change_uses_original_explicit_reboot_policy():
    mgr, ssh = manager()
    assert mgr.reset_and_wait("camera_hard", 120, ord_changed=True) == "reboot"
    mgr.reboot_and_wait.assert_called_once_with(stabilize_sec=120)
    assert not any("cam-recoveryctl" in command for command in _commands(ssh))


def test_recovery_cli_builder_never_uses_deprecated_wrapper():
    for source in (False, True):
        command = _recovery_cli_command(apply_config=source)
        assert "cam_hard_reset.sh" not in command
        assert "timeout 45" not in command
        assert "rc=$?" in command
        assert "PIM_RECOVERY_CLI_RC=%s" in command


def test_terminal_success_parser_returns_strict_request_id():
    assert _parse_recovery_result(
        terminal(), expected_type="camera_hard_reset") == REQUEST_ID

def test_changed_edgeconf_setup_uses_apply_config_not_plain_hard_reset():
    mgr, _ = manager()
    mgr.check_current = MagicMock(return_value=False)
    mgr._snapshot_or_warn = MagicMock()
    mgr.backup = MagicMock(return_value=True)
    mgr.apply_changes = MagicMock()
    mgr.reset_and_wait = MagicMock(return_value="camera_hard")
    mgr.run_setup({
        "edgeconf_changes": {".VHL_CAM.cam_width": 1280},
        "reboot_after": True,
        "reset_mode": "camera_hard",
    })
    mgr.reset_and_wait.assert_called_once_with(
        "camera_hard", 30, ord_changed=False, source_changed=True)


def test_teardown_restored_camera_source_uses_apply_config():
    mgr, _ = manager()
    mgr._restore_conf = MagicMock()
    mgr.reset_and_wait = MagicMock(return_value="camera_hard")
    mgr.run_teardown({
        "edgeconf_changes": {".VHL_CAM.cam_width": 1280},
        "reboot_after": True,
        "reset_mode": "camera_hard",
    })
    mgr._restore_conf.assert_called_once()
    mgr.reset_and_wait.assert_called_once_with(
        "camera_hard", 20, ord_changed=False, source_changed=True)


def test_crash_fault_teardown_does_not_restart_camera_owner():
    from config import load_profile
    from pim_check import PROFILES_DIR
    profile = load_profile(PROFILES_DIR, case="fault_gstapp_crash")
    cleanup = profile["teardown"]["recovery_command"]
    assert "cam-recoveryctl request gstapp_restart" in cleanup
    assert "systemctl restart cam-operate" not in cleanup
