"""tests/test_teardown_readiness.py — teardown 은 setup 의 readiness 기대를 물려받지 않는다 (pim-check#70).

`SetupManager` 는 readiness 상태를 **인스턴스 속성**으로 들고 있다(`_ready_camera_init`,
`_ready_ae_targets`, `_ready_processes_list`, `_ready_recording_paths`). teardown 은
같은 인스턴스에서 재부팅을 타므로 `_stabilize_stages()` 가 그 값을 그대로 승계해,
**방금 끝난 케이스**의 기대값으로 **설정이 복원된** 보드를 게이팅했다.

두 가지가 틀렸다: ① 복원 후 상태를 복원 전 기대값으로 재는 논리적 어긋남,
② AE 정착은 gstApp 기동 +16s 가 필요한데 teardown 예산은 20초 고정이라 들어갈 리가
없어 **매 실행 끝에 20초를 버리고 경고를 찍었다**.

teardown 이 확인해야 할 것은 "보드가 살아 돌아왔는가" 하나뿐이므로 `ssh` 만 남긴다.

⚠ 비우면 안 되는 것: `_config_snapshots` — teardown **복원의 원본**이다(#65/#67).
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from setup import EDGECONF_PATH, SetupManager


def _mgr():
    mgr = SetupManager(MagicMock(), reboot_timeout=300, poll_interval=10)
    mgr._local0_log = MagicMock()
    mgr.restore = MagicMock()
    mgr.restore_from_snapshot = MagicMock(return_value=True)
    mgr.reboot_and_wait = MagicMock()
    return mgr


def _loaded_with_readiness(mgr):
    """setup 이 케이스 기대값으로 readiness 를 채운 상태를 만든다."""
    mgr.run_setup(
        {"inject_command": "true"},
        ready_processes=["gstApp"],
        ready_recording_paths=["/mnt/sd_cam"],
        ready_camera_init=True,
        ready_ae_targets=[{"channel": 0, "value": "0x0100"}],
    )
    return mgr


class TestTeardownDropsSetupReadiness(unittest.TestCase):
    def test_setup_really_does_load_those_expectations(self):
        """전제 확인 — 이 승계가 실재해야 아래 가드가 의미를 갖는다."""
        mgr = _loaded_with_readiness(_mgr())
        names = [n for n, _ in mgr._stabilize_stages()]
        self.assertEqual(
            names,
            ["ssh", "session_anchor", "processes", "camera_init", "ae_settle", "recording"])

    def test_teardown_reboot_waits_only_for_ssh(self):
        """teardown 재부팅이 보는 단계는 ssh 하나여야 한다."""
        mgr = _loaded_with_readiness(_mgr())
        seen = {}
        mgr.reboot_and_wait = MagicMock(
            side_effect=lambda **kw: seen.update(
                stages=[n for n, _ in mgr._stabilize_stages()]))

        mgr.run_teardown({"edgeconf_changes": {".a": 1}, "reboot_after": True})

        mgr.reboot_and_wait.assert_called_once()
        self.assertEqual(seen.get("stages"), ["ssh"])

    def test_restore_source_survives_the_reset(self):
        """복원 원본(_config_snapshots)까지 비우면 teardown 복원이 죽는다 — 지켜야 한다."""
        mgr = _loaded_with_readiness(_mgr())
        mgr._config_snapshots[EDGECONF_PATH] = "eyJhIjogMX0="

        mgr.run_teardown({"edgeconf_changes": {".a": 1}, "reboot_after": True})

        mgr.restore_from_snapshot.assert_called_once_with(EDGECONF_PATH)
        self.assertIn(EDGECONF_PATH, mgr._config_snapshots)

    def test_reset_happens_even_without_reboot(self):
        """재부팅이 없는 teardown 도 상태를 남기면 안 된다 — 다음 케이스가 물려받는다."""
        mgr = _loaded_with_readiness(_mgr())
        mgr.reboot_and_wait = MagicMock()

        mgr.run_teardown({"edgeconf_changes": {".a": 1}})   # reboot_after 없음

        self.assertEqual([n for n, _ in mgr._stabilize_stages()], ["ssh"])


if __name__ == "__main__":
    unittest.main()
