"""
tests/test_learner.py - learner.py 베이스라인 학습 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from learner import learn_baseline


class TestLearnBaseline(unittest.TestCase):
    def _make_ssh(self):
        ssh = MagicMock()
        ssh.host = "192.168.0.5"

        responses = {
            "jq '.VHL_CAM.cam_width' /root/shared_v/edgeconf_pim.json": "1920",
            "jq '.VHL_CAM.cam_height' /root/shared_v/edgeconf_pim.json": "1080",
            "jq '.VHL_CAM.fps' /root/shared_v/edgeconf_pim.json": "30",
            "jq '.VHL_CAM.recording_time' /root/shared_v/edgeconf_pim.json": "60",
            "jq '.VHL_CAM.i2c2.ch0.enable' /root/shared_v/edgeconf_pim.json": "true",
            "jq '.VHL_CAM.i2c2.ch1.enable' /root/shared_v/edgeconf_pim.json": "true",
            "jq '.VHL_CAM.i2c1.ch2.enable' /root/shared_v/edgeconf_pim.json": "false",
            "jq '.VHL_CAM.i2c1.ch3.enable' /root/shared_v/edgeconf_pim.json": "false",
            "ps -C gstApp -o %cpu= 2>/dev/null | head -1 | tr -d ' '": "25.0",
            "cat /sys/devices/virtual/thermal/thermal_zone0/temp": "72000",
            "pgrep -x gstApp": "123",
            "pgrep -x BG_Check_for_pim": "456",
            "pgrep -x chk_cam_operate": "789",
            "pgrep -x ord": None,
            "pgrep -x vcm": None,
            "cat /tmp/cam_state/state 2>/dev/null": "healthy",
            "cat /tmp/cam_state/streak 2>/dev/null": "0",
            "ip -br addr show eth0 2>/dev/null | awk '{print $3}' | cut -d/ -f1": "192.168.0.5",
            "ip -br addr show wlp1s0 2>/dev/null | awk '{print $3}' | cut -d/ -f1": None,
        }
        ssh.run.side_effect = lambda cmd: responses.get(cmd)
        return ssh

    def test_basic_output_structure(self):
        ssh = self._make_ssh()
        result = learn_baseline(ssh, name="test_baseline")
        self.assertIn("test_baseline", result)
        self.assertIn("checks:", result)
        self.assertIn("processes:", result)
        self.assertIn("thermal:", result)
        self.assertIn("cam_state:", result)
        self.assertIn("recording:", result)

    def test_auto_name_when_none(self):
        ssh = self._make_ssh()
        result = learn_baseline(ssh, name=None)
        self.assertIn("learned_", result)

    def test_channels_counted(self):
        ssh = self._make_ssh()
        result = learn_baseline(ssh, name="ch_test")
        self.assertIn("expected_channels: 2", result)
        self.assertIn("2/2", result)

    def test_temperature_thresholds(self):
        ssh = self._make_ssh()
        result = learn_baseline(ssh, name="temp_test")
        # 72C → warn 77, max 82
        self.assertIn("warn_temp_c: 77", result)
        self.assertIn("max_temp_c: 82", result)

    def test_cpu_range(self):
        ssh = self._make_ssh()
        result = learn_baseline(ssh, name="cpu_test")
        # 25% → range [12, 47]
        self.assertIn("gst_range:", result)

    def test_processes_detected(self):
        ssh = self._make_ssh()
        result = learn_baseline(ssh, name="proc_test")
        self.assertIn("gstApp", result)
        self.assertIn("chk_cam_operate", result)

    def test_no_channels_enabled(self):
        ssh = MagicMock()
        ssh.host = "192.168.0.5"
        ssh.run.return_value = None  # 모든 명령 실패
        # cam_state/streak에 대해 None 반환 → or로 기본값 사용
        result = learn_baseline(ssh, name="empty")
        self.assertIn("expected_channels: null", result)

    def test_invalid_cpu_value(self):
        ssh = self._make_ssh()
        # gstApp CPU가 비정상 문자열
        original = ssh.run.side_effect

        def patched(cmd):
            if "ps -C gstApp" in cmd:
                return "N/A"
            return original(cmd)

        ssh.run.side_effect = patched
        result = learn_baseline(ssh, name="bad_cpu")
        self.assertIn("gst_range: [0, 100]", result)

    def test_invalid_temp_value(self):
        ssh = self._make_ssh()
        original = ssh.run.side_effect

        def patched(cmd):
            if "thermal_zone0" in cmd:
                return "invalid"
            return original(cmd)

        ssh.run.side_effect = patched
        result = learn_baseline(ssh, name="bad_temp")
        self.assertIn("warn_temp_c: 80", result)
        self.assertIn("max_temp_c: 85", result)


if __name__ == "__main__":
    unittest.main()
