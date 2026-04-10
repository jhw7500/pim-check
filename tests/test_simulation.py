"""
tests/test_simulation.py - 시뮬레이션 케이스 코드 기반 검증 (1차)
각 fault 케이스의 YAML 로드 + mock SSH로 FAIL/PASS 판정 검증
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

from config import load_profile
from checks.custom import CustomCommandCheck
from checks.process import ProcessCheck

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "profiles")


class TestFaultStorageWrite(unittest.TestCase):
    """저장소 쓰기 불가 시뮬레이션: 쓰기 실패 시 FAIL"""

    def setUp(self):
        self.profile = load_profile(PROFILES_DIR, case="fault_sd_readonly")
        self.check = CustomCommandCheck()

    def test_write_fails_on_readonly(self):
        """touch 명령 실패 → 쓰기 불가 감지"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "touch /tmp" in cmd:
                return None  # 쓰기 실패
            if "touch /root/shared_v" in cmd:
                return "OK"
            if "df / --output=avail" in cmd:
                return "2000000"
            return None

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("read-only", reason)

    def test_write_passes_on_normal(self):
        """모든 쓰기 성공 → PASS"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "touch" in cmd:
                return "OK"
            if "df / --output=avail" in cmd:
                return "2000000"
            return None

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertTrue(passed)

    def test_low_disk_space_fails(self):
        """디스크 공간 부족 → FAIL"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "touch" in cmd:
                return "OK"
            if "df / --output=avail" in cmd:
                return "500000"  # 500MB < 1048576
            return None

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("1GB", reason)


class TestFaultCamChError(unittest.TestCase):
    """카메라 채널 에러 시뮬레이션: ch*_error=true 시 FAIL"""

    def setUp(self):
        self.profile = load_profile(PROFILES_DIR, case="fault_cam_ch_error")
        self.check = CustomCommandCheck()

    def test_all_channels_ok(self):
        """모든 채널 error=false → PASS"""
        ssh = MagicMock()
        ssh.run.return_value = "false"
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertTrue(passed)

    def test_ch0_error_detected(self):
        """ch0_error=true → FAIL"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "ch0_error" in cmd:
                return "true"
            return "false"

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("channel 0", reason)

    def test_ch2_error_detected(self):
        """ch2_error=true → FAIL"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "ch2_error" in cmd:
                return "true"
            return "false"

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("channel 2", reason)


class TestFaultGstAppCrash(unittest.TestCase):
    """gstApp 크래시 시뮬레이션: 프로세스 미실행 시 FAIL"""

    def setUp(self):
        self.profile = load_profile(PROFILES_DIR, case="fault_gstapp_crash")
        self.check = ProcessCheck()

    def test_gstapp_missing_fails(self):
        """gstApp 미실행 → process check FAIL"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "pgrep -x gstApp" in cmd:
                return None  # not running
            if "pgrep -x chk_cam_operate" in cmd:
                return "123"
            if "ps -C chk_cam_operate" in cmd:
                return "0.5"
            return None

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("gstApp", reason)

    def test_gstapp_running_passes(self):
        """gstApp 실행 중 → PASS"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "pgrep -x" in cmd:
                return "123"
            if "ps -C" in cmd:
                return "30.0"
            return None

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertTrue(passed)


class TestFaultHighCpu(unittest.TestCase):
    """CPU 이상 시뮬레이션: 범위 벗어남 감지"""

    def setUp(self):
        self.profile = load_profile(PROFILES_DIR, case="fault_high_cpu")
        self.check = ProcessCheck()

    def test_gst_cpu_over_range_fails(self):
        """gstApp CPU 95% > max 80% → FAIL"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "pgrep -x" in cmd:
                return "123"
            if "ps -C gstApp" in cmd:
                return "95.0"
            if "ps -C" in cmd:
                return "1.0"
            return None

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("gstApp", reason)

    def test_gst_cpu_under_range_fails(self):
        """gstApp CPU 2% < min 10% → FAIL"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "pgrep -x" in cmd:
                return "123"
            if "ps -C gstApp" in cmd:
                return "2.0"
            if "ps -C" in cmd:
                return "1.0"
            return None

        ssh.run.side_effect = side_effect
        config = self.profile["checks"]
        data = self.check.collect(ssh, config)
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("gstApp", reason)


if __name__ == "__main__":
    unittest.main()
