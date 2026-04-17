from __future__ import annotations
"""
tests/test_setup.py - SetupManager 단위 테스트
"""
import unittest
from unittest.mock import patch, MagicMock

from setup import SetupManager, EDGECONF_PATH, EDGECONF_BACKUP, EDGECONF_BACKUP_DIR


class TestBackupEdgeconf(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh)

    def test_backup_edgeconf(self):
        """backup()이 분리된 디렉토리를 생성하고 cp하는지 확인"""
        self.ssh.run.return_value = "OK"
        result = self.mgr.backup()
        self.ssh.run.assert_called_once_with(
            f"mkdir -p {EDGECONF_BACKUP_DIR} && cp {EDGECONF_PATH} {EDGECONF_BACKUP} && echo OK"
        )
        self.assertTrue(result)

    def test_backup_path_not_in_shared_v_root(self):
        """백업 경로가 /root/shared_v 루트에 있으면 안 됨 (glob 충돌 방지)."""
        # EDGECONF_BACKUP_DIR는 /root/shared_v의 하위(숨김) 디렉토리여야 함
        self.assertIn("/root/shared_v/", EDGECONF_BACKUP_DIR)
        self.assertNotEqual(EDGECONF_BACKUP_DIR, "/root/shared_v")
        # 백업 파일명이 edgeconf_*.json glob 패턴에 매칭되지 않아야 함
        import fnmatch
        import os
        backup_basename = os.path.basename(EDGECONF_BACKUP)
        # edgeconf_*.json 패턴에 매칭되면 안 됨 (update_network_pim.py glob 충돌)
        # 현재 파일명 edgeconf_pim.json.bak은 .json으로 끝나지 않으므로 안전
        self.assertFalse(
            fnmatch.fnmatch(backup_basename, "edgeconf_*.json"),
            f"백업 파일명 '{backup_basename}'이 edgeconf_*.json 패턴에 매칭됨 (glob 충돌)"
        )


class TestApplyEdgeconfChanges(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh)

    def test_apply_edgeconf_changes(self):
        """apply_changes()가 각 변경에 대해 jq 명령을 실행하는지 확인"""
        changes = {
            ".VHL_CAM.cam_width": 1920,
            ".VHL_CAM.cam_height": 1080,
        }
        self.mgr.apply_changes(changes)
        self.assertEqual(self.ssh.run.call_count, 2)

        calls = [str(c) for c in self.ssh.run.call_args_list]
        # 두 호출 모두 jq 명령을 포함해야 함
        for c in calls:
            self.assertIn("jq", c)
            self.assertIn(EDGECONF_PATH, c)


class TestRestoreEdgeconf(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh)

    def test_restore_edgeconf(self):
        """restore()가 백업 파일을 원래 위치로 복사하는지 확인"""
        self.mgr.restore()
        self.ssh.run.assert_called_once_with(f"cp {EDGECONF_BACKUP} {EDGECONF_PATH}")


class TestRebootAndWait(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh, reboot_timeout=300, poll_interval=5)

    @patch("setup.time.sleep")
    def test_reboot_and_wait(self, mock_sleep):
        """check_connectivity가 False 두 번 후 True 반환 시 정상 완료 확인"""
        self.ssh.check_connectivity.side_effect = [False, False, True]
        self.mgr.reboot_and_wait(stabilize_sec=5)
        # reboot 명령이 호출되었는지 확인
        self.ssh.run.assert_called_once_with("reboot")
        # check_connectivity가 세 번 호출되었는지 확인
        self.assertEqual(self.ssh.check_connectivity.call_count, 3)
        # sleep이 호출되었는지 확인 (초기 5초 + 폴링 간격 + stabilize)
        self.assertTrue(mock_sleep.called)

    @patch("setup.time.sleep")
    def test_reboot_timeout_raises(self, mock_sleep):
        """check_connectivity가 계속 False일 때 TimeoutError 발생 확인"""
        self.ssh.check_connectivity.return_value = False
        mgr = SetupManager(self.ssh, reboot_timeout=10, poll_interval=2)
        with self.assertRaises(TimeoutError):
            mgr.reboot_and_wait(stabilize_sec=5)


class TestCheckCurrent(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh)

    def test_config_already_matches(self):
        """현재 설정이 목표와 일치하면 True 반환"""
        def side_effect(cmd):
            if "cam_width" in cmd:
                return "1280"
            if "cam_height" in cmd:
                return "720"
            if "ch2.enable" in cmd:
                return "false"
            return None

        self.ssh.run.side_effect = side_effect
        changes = {
            ".VHL_CAM.cam_width": 1280,
            ".VHL_CAM.cam_height": 720,
            ".VHL_CAM.i2c1.ch2.enable": False,
        }
        self.assertTrue(self.mgr.check_current(changes))

    def test_config_differs(self):
        """현재 설정이 목표와 다르면 False 반환"""
        def side_effect(cmd):
            if "cam_width" in cmd:
                return "1920"  # 목표는 1280
            return "720"

        self.ssh.run.side_effect = side_effect
        changes = {".VHL_CAM.cam_width": 1280, ".VHL_CAM.cam_height": 720}
        self.assertFalse(self.mgr.check_current(changes))

    @patch("setup.time.sleep")
    def test_run_setup_skips_when_matched(self, mock_sleep):
        """설정이 이미 맞으면 backup/apply/reboot 안 함"""
        self.ssh.run.return_value = "1280"
        self.mgr.run_setup({
            "edgeconf_changes": {".VHL_CAM.cam_width": 1280},
            "reboot_after": True,
        })
        # backup이 호출되지 않아야 함 (check_current만 호출)
        calls = [str(c) for c in self.ssh.run.call_args_list]
        self.assertFalse(any("cp " in c for c in calls))


if __name__ == "__main__":
    unittest.main()
