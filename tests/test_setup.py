from __future__ import annotations
"""
tests/test_setup.py - SetupManager 단위 테스트
"""
import unittest
from unittest.mock import patch, MagicMock

from setup import SetupManager, EDGECONF_PATH, EDGECONF_BACKUP


class TestBackupEdgeconf(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh)

    def test_backup_edgeconf(self):
        """backup()이 백업 cp 명령을 실행하는지 확인"""
        self.ssh.run.return_value = "OK"
        result = self.mgr.backup()
        self.assertEqual(self.ssh.run.call_count, 1)
        cmd = self.ssh.run.call_args[0][0]
        self.assertIn(f"cp {EDGECONF_PATH} {EDGECONF_BACKUP}", cmd)
        self.assertIn("echo OK", cmd)
        self.assertTrue(result)


class TestApplyEdgeconfChanges(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh)

    def test_apply_edgeconf_changes(self):
        """apply_changes()가 각 변경에 대해 jq write + read-back verify를 실행하는지 확인"""
        changes = {
            ".VHL_CAM.cam_width": 1920,
            ".VHL_CAM.cam_height": 1080,
        }

        def side_effect(cmd, **kwargs):
            # write 명령(> /tmp 포함)은 None, read-back verify는 적용값 반환
            if "> /tmp" in cmd:
                return None
            if "cam_width" in cmd:
                return "1920"
            if "cam_height" in cmd:
                return "1080"
            return None

        self.ssh.run.side_effect = side_effect
        self.mgr.apply_changes(changes)
        # 변경당 write 1회 + read-back verify 1회 = 총 4회
        self.assertEqual(self.ssh.run.call_count, 4)

        write_calls = [str(c) for c in self.ssh.run.call_args_list if "> /tmp" in str(c)]
        self.assertEqual(len(write_calls), 2)
        for c in write_calls:
            self.assertIn("jq", c)
            self.assertIn(EDGECONF_PATH, c)

    def test_bool_change_uses_json_boolean_literal(self):
        """bool을 Python 정수나 문자열이 아닌 jq boolean literal로 기록한다."""
        self.ssh.run.side_effect = [None, "false"]

        self.mgr.apply_changes({".feature.enabled": False})

        write_command = self.ssh.run.call_args_list[0].args[0]
        self.assertIn("jq '.feature.enabled = false'", write_command)
        self.assertNotIn("--arg", write_command)

    def test_structured_changes_use_argjson(self):
        """list/dict를 JSON 문자열이 아니라 원래 JSON 타입으로 기록한다."""
        cases = (
            ("list", ["alpha", 2], '["alpha", 2]', '["alpha",2]'),
            ("dict", {"enabled": True}, '{"enabled": true}', '{"enabled":true}'),
        )
        for label, value, shell_json, readback in cases:
            with self.subTest(label=label):
                self.ssh.reset_mock(side_effect=True)
                self.ssh.run.side_effect = [None, readback]

                self.mgr.apply_changes({".feature.value": value})

                write_command = self.ssh.run.call_args_list[0].args[0]
                self.assertIn(f"jq --argjson v '{shell_json}'", write_command)

    def test_string_change_shell_escapes_apostrophe(self):
        """작은따옴표가 있는 문자열도 셸 인용을 깨지 않고 jq에 전달한다."""
        self.ssh.run.side_effect = [None, '"O\'Brien"']

        self.mgr.apply_changes({".device.name": "O'Brien"})

        write_command = self.ssh.run.call_args_list[0].args[0]
        self.assertIn("jq --arg v 'O'\\''Brien'", write_command)


class TestRestoreEdgeconf(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh)

    def test_restore_edgeconf(self):
        """restore()가 백업 파일을 원래 위치로 복사하는지 확인"""
        self.mgr.restore()
        self.assertEqual(self.ssh.run.call_count, 1)
        cmd = self.ssh.run.call_args[0][0]
        self.assertIn(f"cp {EDGECONF_BACKUP} {EDGECONF_PATH}", cmd)


class TestNetworkProbe(unittest.TestCase):
    def setUp(self):
        self.mgr = SetupManager(MagicMock())

    @patch("setup.subprocess.run")
    def test_ping_returns_true_on_zero_exit(self, mock_run):
        mock_run.return_value.returncode = 0

        self.assertTrue(self.mgr._ping("192.0.2.1", count=2, timeout=3))

        mock_run.assert_called_once_with(
            ["ping", "-c", "2", "-W", "3", "192.0.2.1"],
            capture_output=True,
            timeout=10,
        )

    @patch("setup.subprocess.run", side_effect=OSError("ping unavailable"))
    def test_ping_returns_false_when_command_fails(self, mock_run):
        self.assertFalse(self.mgr._ping("192.0.2.1"))
        mock_run.assert_called_once()


class TestRebootAndWait(unittest.TestCase):
    def setUp(self):
        self.ssh = MagicMock()
        self.mgr = SetupManager(self.ssh, reboot_timeout=300, poll_interval=5)

    @patch("setup.time.sleep")
    def test_reboot_and_wait(self, mock_sleep):
        """check_connectivity가 False 두 번 후 True 반환 시 정상 완료 확인.
        복귀 후 단계별 stabilize(1차 SSH readiness)도 connectivity 를 재폴링한다."""
        states = iter([False, False])
        self.ssh.check_connectivity.side_effect = lambda: next(states, True)
        self.mgr.reboot_and_wait(stabilize_sec=5)
        # reboot 명령이 호출되었는지 확인
        self.ssh.run.assert_called_once_with("reboot")
        # 복귀 폴링(F,F,T = 3회) + stabilize 단계의 SSH readiness 재폴링 → 3회 이상
        self.assertGreaterEqual(self.ssh.check_connectivity.call_count, 3)
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
        def side_effect(cmd, **kwargs):
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
        def side_effect(cmd, **kwargs):
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
