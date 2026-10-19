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

    # bool·숫자·리스트·dict·따옴표 문자열을 한 번에 담는 대표 변경 (bps 배열 직렬화
    # 회귀를 막는 list/dict 와 셸 인용을 깨는 작은따옴표를 반드시 포함한다).
    CHANGES = {
        ".VHL_CAM.cam_width": 1920,
        ".VHL_CAM.fps": 15.5,
        ".VHL_CAM.capture.enable": False,
        ".VHL_CAM.i2c2.ch0.bps": [2048, 1024],
        ".VHL_CAM.meta": {"enabled": True, "tags": ["a", "b"]},
        ".VHL_CAM.name": "O'Brien \"cam\" $HOME",
    }

    @staticmethod
    def _readback_for(changes) -> str:
        """보드 jq 가 돌려줄 read-back 출력 — 경로마다 tojson 한 문자열의 배열."""
        import json
        return json.dumps([json.dumps(v, separators=(",", ":")) for v in changes.values()])

    def test_apply_batches_into_one_write_and_one_readback(self):
        """변경 수와 무관하게 SSH 는 write 1회 + read-back 1회만 쓴다 (케이스당 수십 초 절약)."""
        self.ssh.run.side_effect = [None, self._readback_for(self.CHANGES)]

        self.mgr.apply_changes(self.CHANGES)

        self.assertEqual(self.ssh.run.call_count, 2)
        write_command = self.ssh.run.call_args_list[0].args[0]
        self.assertIn(EDGECONF_PATH, write_command)
        self.assertIn("&& mv ", write_command)
        for path in self.CHANGES:
            self.assertIn(path, write_command)

    def test_write_command_produces_original_json_types(self):
        """생성된 write 명령을 실제 bash+jq 로 돌려 원래 JSON 타입 그대로 기록되는지 본다."""
        import json
        import os
        import shutil
        import subprocess
        import tempfile
        if not (shutil.which("jq") and shutil.which("bash")):
            self.skipTest("local jq/bash unavailable")
        self.ssh.run.side_effect = [None, self._readback_for(self.CHANGES)]
        self.mgr.apply_changes(self.CHANGES)
        write_command = self.ssh.run.call_args_list[0].args[0]

        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "conf.json")
            with open(conf, "w", encoding="utf-8") as f:
                json.dump({"VHL_CAM": {"i2c2": {"ch0": {"bps": [1, 1]}}, "keep": 7}}, f)
            local = (write_command.replace(EDGECONF_PATH, conf)
                     .replace("/tmp/_conf_tmp.json", os.path.join(tmp, "out.json")))
            subprocess.run(["bash", "-c", local], check=True)
            with open(conf, encoding="utf-8") as f:
                doc = json.load(f)

        cam = doc["VHL_CAM"]
        self.assertEqual(cam["cam_width"], 1920)
        self.assertEqual(cam["fps"], 15.5)
        self.assertIs(cam["capture"]["enable"], False)
        self.assertEqual(cam["i2c2"]["ch0"]["bps"], [2048, 1024])
        self.assertEqual(cam["meta"], {"enabled": True, "tags": ["a", "b"]})
        self.assertEqual(cam["name"], "O'Brien \"cam\" $HOME")
        self.assertEqual(cam["keep"], 7)

    def test_readback_mismatch_names_the_key(self):
        """read-back 값이 하나라도 다르면 그 경로를 담아 RuntimeError."""
        import json
        readback = json.loads(self._readback_for(self.CHANGES))
        readback[3] = "[1024,1024]"   # .VHL_CAM.i2c2.ch0.bps 가 반영 안 됨
        self.ssh.run.side_effect = [None, json.dumps(readback)]

        with self.assertRaisesRegex(RuntimeError, r"\.VHL_CAM\.i2c2\.ch0\.bps"):
            self.mgr.apply_changes(self.CHANGES)

    def test_readback_unavailable_raises(self):
        """read-back 이 실패(None)하거나 형식이 틀리면 적용 성공으로 보지 않는다."""
        for label, out in (("none", None), ("garbage", "not json"), ("short", '["1920"]')):
            with self.subTest(label=label):
                self.ssh.reset_mock(side_effect=True)
                self.ssh.run.side_effect = [None, out]
                with self.assertRaises(RuntimeError):
                    self.mgr.apply_changes(self.CHANGES)

    def test_empty_changes_make_no_ssh_call(self):
        self.mgr.apply_changes({})
        self.ssh.run.assert_not_called()


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

    def test_run_setup_resets_same_boot_anchor_preservation(self):
        self.mgr._preserve_session_anchor = True

        changed = self.mgr.run_setup({})

        self.assertFalse(changed)
        self.assertFalse(self.mgr._preserve_session_anchor)

    @patch("setup.time.sleep")
    def test_run_setup_skips_when_matched(self, mock_sleep):
        """camera-hard 설정이 이미 맞으면 앵커만 갱신하고 reset/reboot 안 함."""
        def side_effect(command, **_kwargs):
            if "date '+%Y-%m-%d %H:%M:%S'" in command:
                return "2026-09-01 12:00:00\nboot-id"
            return "1280"

        self.ssh.run.side_effect = side_effect
        changed = self.mgr.run_setup({
            "edgeconf_changes": {".VHL_CAM.cam_width": 1280},
            "reboot_after": True,
            "reset_mode": "camera_hard",
        }, ready_camera_init=True)

        commands = [call.args[0] for call in self.ssh.run.call_args_list]
        self.assertFalse(changed)
        self.assertEqual(
            sum("date '+%Y-%m-%d %H:%M:%S'" in command for command in commands),
            1,
        )
        self.assertFalse(any("cp " in command for command in commands))
        self.assertFalse(any("cam_hard_reset.sh" in command for command in commands))
        self.assertFalse(any(command == "reboot" for command in commands))

    def test_run_setup_matched_camera_hard_raises_when_anchor_refresh_fails(self):
        self.ssh.run.side_effect = ["1280", "missing boot id"]

        with self.assertRaisesRegex(
            RuntimeError, "failed to refresh same-boot session anchor"
        ):
            self.mgr.run_setup({
                "edgeconf_changes": {".VHL_CAM.cam_width": 1280},
                "reboot_after": True,
                "reset_mode": "camera_hard",
            }, ready_camera_init=True)


if __name__ == "__main__":
    unittest.main()
