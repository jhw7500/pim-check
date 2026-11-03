"""Only reviewed camera-functional plans opt into fail-closed setup snapshots.

Offline-only: no SSH to a board, config writes, reboots or sensor commands.
"""
from unittest.mock import MagicMock

import pytest

from plan import load_plan
from setup import EDGECONF_PATH, ORD_VCM_PATH, SetupManager


def manager():
    mgr = SetupManager(MagicMock(), reboot_timeout=300, poll_interval=10)
    mgr.check_current = MagicMock(return_value=False)
    mgr.backup = MagicMock(return_value=True)
    mgr.snapshot_config = MagicMock(return_value=True)
    mgr.apply_changes = MagicMock()
    mgr._local0_log = MagicMock()
    return mgr


def test_camera_smoke_and_channels_require_strict_original_snapshot():
    for name in ("smoke_camera_functional", "channel_camera_functional"):
        plan = load_plan(f"profiles/plans/{name}.yaml")
        assert plan.case_overrides["setup"]["require_snapshot"] is True


@pytest.mark.parametrize("path,config", [
    (EDGECONF_PATH, {"edgeconf_changes": {".VHL_CAM.fps": 15}}),
    (ORD_VCM_PATH, {"ord_vcm_changes": {".VCM.srt_enable": True}}),
])
def test_failed_original_snapshot_aborts_before_backup_apply_or_reboot(path, config):
    mgr = manager()
    mgr.snapshot_config.return_value = False
    mgr.reset_and_wait = MagicMock()
    with pytest.raises(RuntimeError, match="strict camera snapshot unavailable"):
        mgr.run_setup({"require_snapshot": True, "reboot_after": True, **config})
    mgr.snapshot_config.assert_called_once_with(path)
    mgr.backup.assert_not_called()
    mgr.apply_changes.assert_not_called()
    mgr.reset_and_wait.assert_not_called()
    assert mgr._config_snapshots == {}
    assert path in mgr._snapshot_failures


def test_second_config_snapshot_failure_never_applies_first_config():
    mgr = manager()
    seen = []
    mgr.snapshot_config.side_effect = lambda path: (
        seen.append(("snapshot", path))
        or path == EDGECONF_PATH
    )
    mgr.backup.side_effect = lambda path: seen.append(("backup", path)) or True
    mgr.apply_changes.side_effect = lambda changes, path: seen.append(("apply", path))
    cfg = {
        "require_snapshot": True,
        "edgeconf_changes": {".VHL_CAM.fps": 15},
        "ord_vcm_changes": {".VCM.srt_enable": True},
    }
    with pytest.raises(RuntimeError, match="strict camera snapshot unavailable"):
        mgr.run_setup(cfg)
    assert seen == [
        ("snapshot", EDGECONF_PATH), ("snapshot", ORD_VCM_PATH)
    ]
    assert mgr._config_snapshots == {}
    mgr.backup.assert_not_called()
    mgr.apply_changes.assert_not_called()


def test_both_config_snapshots_finish_before_any_mutation():
    mgr = manager()
    seen = []
    mgr.snapshot_config.side_effect = (
        lambda path: seen.append(("snapshot", path)) or True
    )
    mgr.backup.side_effect = lambda path: seen.append(("backup", path)) or True
    mgr.apply_changes.side_effect = lambda changes, path: seen.append(("apply", path))
    cfg = {
        "require_snapshot": True,
        "edgeconf_changes": {".VHL_CAM.fps": 15},
        "ord_vcm_changes": {".VCM.srt_enable": True},
    }
    assert mgr.run_setup(cfg) is True
    assert seen[:2] == [
        ("snapshot", EDGECONF_PATH), ("snapshot", ORD_VCM_PATH)
    ]
    assert seen[2:] == [
        ("backup", EDGECONF_PATH), ("apply", EDGECONF_PATH),
        ("backup", ORD_VCM_PATH), ("apply", ORD_VCM_PATH),
    ]


def test_strict_backup_failure_is_reported_not_silently_returned_false():
    mgr = manager()
    mgr.backup.return_value = False
    with pytest.raises(RuntimeError, match="strict camera edgeconf backup failed"):
        mgr.run_setup({
            "require_snapshot": True,
            "edgeconf_changes": {".VHL_CAM.fps": 15},
        })
    mgr.apply_changes.assert_not_called()


def test_legacy_snapshot_failure_still_only_warns():
    mgr = manager()
    mgr.snapshot_config.return_value = False
    assert mgr.run_setup({"edgeconf_changes": {".VHL_CAM.fps": 15}}) is True
    mgr.apply_changes.assert_called_once()


def test_strict_mode_config_matching_does_not_touch_or_reboot():
    mgr = manager()
    mgr.check_current.return_value = True
    assert mgr.run_setup({
        "require_snapshot": True,
        "reboot_after": True,
        "edgeconf_changes": {".VHL_CAM.fps": 15},
    }) is False
    mgr.snapshot_config.assert_not_called()
    mgr.apply_changes.assert_not_called()


@pytest.mark.parametrize("value", ["yes", 1, None, [], {}])
def test_strict_mode_rejects_untyped_values(value):
    mgr = manager()
    with pytest.raises(ValueError, match="require_snapshot must be a boolean"):
        mgr.run_setup({
            "require_snapshot": value,
            "edgeconf_changes": {".VHL_CAM.fps": 15},
        })
    mgr.snapshot_config.assert_not_called()
    mgr.apply_changes.assert_not_called()
