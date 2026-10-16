"""Strict acceptance validates actual camera13 recovery history, not only rc=0."""
from __future__ import annotations

import copy
import json
import shlex
from unittest.mock import MagicMock

import pytest

from setup import (
    RECOVERY_AUDIT_PATH,
    RECOVERY_HISTORY_DIR,
    RECOVERY_OWNER_PATH,
    SetupManager,
    _assert_hardware_recovery,
    _camera_owner,
)


ID = "01234567-89ab-cdef-0123-456789abcdef"
ANCHOR = "2026-10-15 12:00:00\nboot-id"


def owner(**overrides):
    data = {
        "boot_id": "boot-id",
        "invocation_id": "invocation-123",
        "token": "private-daemon-token",
        "pid": 101,
        "proc_start_time": "2075",
        "lifecycle": "ACTIVE",
    }
    data.update(overrides)
    return data


def history(*, request_type="apply_config", action="camera_hard_reset"):
    data = {
        "request": {
            "id": ID,
            "type": request_type,
            "source": "pim-check",
            "status": "SUCCEEDED",
            "rc": 0,
            "owner": owner(),
        },
        "actions": [{
            "action": action,
            "request_id": ID,
            "status": "SUCCEEDED",
            "rc": 0,
            "started_at": 1728990001,
            "finished_at": 1728990005,
        }],
    }
    return data


def strict_manager(hist=None, *, owner_after=None):
    ssh = MagicMock()
    evidence = {}

    def respond(command, **_kwargs):
        if command == f"cat {RECOVERY_OWNER_PATH}":
            evidence["owner_reads"] = evidence.get("owner_reads", 0) + 1
            is_after = evidence["owner_reads"] > 1
            return json.dumps(owner_after if is_after and owner_after else owner())
        if "pim_check_anchor" in command:
            return ANCHOR
        if "cam-recoveryctl apply-config" in command:
            return (
                f"CAM_RECOVERY_RESULT id={ID} type=apply_config "
                "status=SUCCEEDED rc=0\nPIM_RECOVERY_CLI_RC=0"
            )
        if "cam-recoveryctl request camera_hard_reset" in command:
            return (
                f"CAM_RECOVERY_RESULT id={ID} type=camera_hard_reset "
                "status=SUCCEEDED rc=0\nPIM_RECOVERY_CLI_RC=0"
            )
        if command == f"cat {RECOVERY_HISTORY_DIR}/{ID}.json":
            return json.dumps(hist if hist is not None else history())
        if command.startswith("printf ") and RECOVERY_AUDIT_PATH in command:
            payload = shlex.split(command)[2]
            evidence["marker"] = json.loads(payload)
            return payload
        raise AssertionError(f"unexpected SSH command: {command}")

    ssh.run.side_effect = respond
    mgr = SetupManager(ssh)
    mgr._stabilize = MagicMock()
    mgr.reboot_and_wait = MagicMock()
    mgr._local0_log = MagicMock()
    return mgr, ssh, evidence


def test_strict_camera13_acceptance_records_verified_action_and_owner():
    mgr, ssh, evidence = strict_manager()
    result = mgr.reset_and_wait(
        "camera_hard", 120, ord_changed=False, source_changed=True,
        require_hard_reset=True,
    )
    assert result == "camera_hard"
    assert evidence["owner_reads"] == 2
    assert evidence["marker"]["request_id"] == ID
    assert evidence["marker"]["type"] == "apply_config"
    assert evidence["marker"]["owner"]["invocation_id"] == "invocation-123"
    assert "token" not in evidence["marker"]["owner"]
    commands = [call.args[0] for call in ssh.run.call_args_list]
    assert commands.index(f"cat {RECOVERY_OWNER_PATH}") == 0
    assert commands.count(f"cat {RECOVERY_OWNER_PATH}") == 2
    assert f"cat {RECOVERY_HISTORY_DIR}/{ID}.json" in commands
    assert "cam_hard_reset.sh" not in str(commands)
    mgr._stabilize.assert_called_once_with(120)
    mgr.reboot_and_wait.assert_not_called()


@pytest.mark.parametrize("mutation,description", [
    (lambda h: h["actions"].clear(), "no actions"),
    (lambda h: h["actions"][0].update(action="gstapp_restart"), "consumer only"),
    (lambda h: h["actions"][0].update(action="reboot_fallback"), "fallback"),
    (lambda h: h["actions"][0].update(status="FAILED", rc=70), "failed action"),
    (lambda h: h["request"].update(status="FAILED", rc=70), "failed request"),
    (lambda h: h["request"].update(id="wrong"), "wrong request"),
    (lambda h: h["request"].update(source="operator"), "wrong source"),
    (lambda h: h["request"]["owner"].update(invocation_id="old"), "changed request owner"),
    (lambda h: h["actions"][0].update(request_id="wrong"), "foreign action"),
])
def test_history_failure_cannot_be_misreported_as_hard_reset(mutation, description):
    payload = history()
    mutation(payload)
    mgr, ssh, evidence = strict_manager(payload)
    with pytest.raises(RuntimeError, match="no competing reboot"):
        mgr.reset_and_wait(
            "camera_hard", 120, ord_changed=False, source_changed=True,
            require_hard_reset=True,
        )
    assert "marker" not in evidence, description
    mgr.reboot_and_wait.assert_not_called()
    mgr._stabilize.assert_not_called()
    assert sum(
        "cam-recoveryctl apply-config" in call.args[0]
        for call in ssh.run.call_args_list
    ) == 1


def test_terminal_result_can_precede_owner_active_transition():
    """The CLI terminal line may be visible before cam-operate returns ACTIVE."""
    from unittest.mock import patch

    mgr, ssh, evidence = strict_manager()
    original = ssh.run.side_effect

    def recovering_once(command, **kwargs):
        result = original(command, **kwargs)
        if (command == f"cat {RECOVERY_OWNER_PATH}"
                and evidence["owner_reads"] == 2):
            return json.dumps(owner(lifecycle="APPLYING_CONFIG"))
        return result

    ssh.run.side_effect = recovering_once
    with patch("setup.time.sleep") as sleep:
        assert mgr.reset_and_wait(
            "camera_hard", 120, ord_changed=False, source_changed=True,
            require_hard_reset=True,
        ) == "camera_hard"
    sleep.assert_called_once_with(1)
    assert evidence["owner_reads"] == 3
    mgr.reboot_and_wait.assert_not_called()


def test_owner_invocation_change_rejected_and_no_second_reset():
    mgr, ssh, evidence = strict_manager(
        owner_after=owner(invocation_id="new-daemon-invocation"))
    with pytest.raises(RuntimeError, match="no competing reboot"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=False,
                           source_changed=True, require_hard_reset=True)
    assert "marker" not in evidence
    mgr.reboot_and_wait.assert_not_called()


def test_owner_lifecycle_not_active_prevents_request():
    mgr, ssh, evidence = strict_manager()
    ssh.run.side_effect = lambda cmd, **kw: (
        json.dumps(owner(lifecycle="RECOVERING"))
        if cmd == f"cat {RECOVERY_OWNER_PATH}" else
        (_ for _ in ()).throw(AssertionError("should not call recovery"))
    )
    with pytest.raises(RuntimeError, match="owner preflight failed"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=False,
                           require_hard_reset=True)
    mgr.reboot_and_wait.assert_not_called()


def test_strict_anchor_failure_fails_closed_without_reboot():
    mgr, ssh, evidence = strict_manager()
    old = ssh.run.side_effect

    def fail_anchor(command, **kwargs):
        if "pim_check_anchor" in command:
            return "bad anchor"
        return old(command, **kwargs)

    ssh.run.side_effect = fail_anchor
    with pytest.raises(RuntimeError, match="acceptance aborted"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=False,
                           require_hard_reset=True)
    mgr.reboot_and_wait.assert_not_called()
    assert "marker" not in evidence


def test_strict_mode_cannot_downgrade_to_reboot():
    mgr, _, _ = strict_manager()
    with pytest.raises(RuntimeError, match="camera-only"):
        mgr.reset_and_wait("camera_hard", 120, ord_changed=True,
                           require_hard_reset=True)
    mgr.reboot_and_wait.assert_not_called()


def test_matching_config_is_not_a_hardware_reset_acceptance():
    mgr, _, _ = strict_manager()
    mgr.check_current = MagicMock(return_value=True)
    with pytest.raises(RuntimeError, match="no hardware reset executed"):
        mgr.run_setup({
            "edgeconf_changes": {".VHL_CAM.cam_width": 1280},
            "reboot_after": True,
            "reset_mode": "camera_hard",
            "require_hard_reset": True,
        })
    mgr.reboot_and_wait.assert_not_called()


def test_setup_opt_in_enables_exact_history_contract_only_in_camera_hard_mode():
    mgr, _, _ = strict_manager()
    mgr.check_current = MagicMock(return_value=False)
    mgr._snapshot_or_warn = MagicMock()
    mgr.backup = MagicMock(return_value=True)
    mgr.apply_changes = MagicMock()
    mgr.reset_and_wait = MagicMock(return_value="camera_hard")
    mgr.run_setup({
        "edgeconf_changes": {".VHL_CAM.cam_width": 1280},
        "reboot_after": True,
        "reset_mode": "camera_hard",
        "require_hard_reset": True,
    })
    mgr.reset_and_wait.assert_called_once_with(
        "camera_hard", 30, ord_changed=False,
        source_changed=True, require_hard_reset=True,
    )


def test_camera_owner_parser_requires_valid_immutable_fields():
    parsed = _camera_owner(json.dumps(owner()))
    assert parsed["invocation_id"] == "invocation-123"
    for broken in ("", "null", "[]", "{}", '{"pid":-1}'):
        with pytest.raises(RuntimeError):
            _camera_owner(broken)


def test_explicit_reset_action_must_be_real():
    h = history(request_type="camera_hard_reset")
    _assert_hardware_recovery(
        json.dumps(h), ID, "camera_hard_reset", owner(), owner())
    bad = copy.deepcopy(h)
    bad["actions"].append({
        "action": "reboot_fallback", "request_id": ID,
        "status": "SUCCEEDED", "rc": 0,
    })
    with pytest.raises(RuntimeError, match="reboot fallback"):
        _assert_hardware_recovery(
            json.dumps(bad), ID, "camera_hard_reset", owner(), owner())
