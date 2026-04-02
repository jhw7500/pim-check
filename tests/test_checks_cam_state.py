"""
tests/test_checks_cam_state.py - CamStateCheck 단위 테스트 (실제 타겟 구조 반영)
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.cam_state import CamStateCheck


class TestCamStateCheckCollect(unittest.TestCase):
    def setUp(self):
        self.check = CamStateCheck()
        self.config = {
            "cam_state": {
                "dir": "/tmp/cam_state",
                "valid_states": ["healthy", "degraded", "recovering", "failed"],
                "expected_state": "healthy",
                "max_streak": 0,
            }
        }

    def test_collect_reads_state_and_streak(self):
        """state, streak, channels/ch*_error를 올바르게 읽는지 확인"""
        ssh = MagicMock()

        def side_effect(cmd):
            if cmd.endswith("/state"):
                return "healthy"
            if cmd.endswith("/streak"):
                return "0"
            if "ls " in cmd and "channels" in cmd:
                return "ch0_error\nch0_last_ok\nch1_error\nch1_last_ok"
            if "ch0_error" in cmd:
                return "false"
            if "ch1_error" in cmd:
                return "false"
            return None

        ssh.run.side_effect = side_effect

        data = self.check.collect(ssh, self.config)

        self.assertEqual(data["states"]["state"], "healthy")
        self.assertEqual(data["streaks"]["streak"], 0)
        self.assertIn("ch0_error", data["channels"])
        self.assertIn("ch1_error", data["channels"])

    def test_collect_state_not_found(self):
        """state 파일이 없으면 error 반환"""
        ssh = MagicMock()
        ssh.run.return_value = None
        data = self.check.collect(ssh, self.config)
        self.assertIn("error", data)


class TestCamStateCheckValidate(unittest.TestCase):
    def setUp(self):
        self.check = CamStateCheck()
        self.config = {
            "cam_state": {
                "dir": "/tmp/cam_state",
                "valid_states": ["healthy", "degraded", "recovering", "failed"],
                "expected_state": "healthy",
                "max_streak": 0,
            }
        }

    def test_validate_healthy_passes(self):
        data = {
            "states": {"state": "healthy"},
            "streaks": {"streak": 0},
            "channels": {"ch0_error": "false"},
        }
        passed, reason = self.check.validate(data, self.config)
        self.assertTrue(passed)
        self.assertEqual(reason, "OK")

    def test_validate_unexpected_state_fails(self):
        data = {
            "states": {"state": "failed"},
            "streaks": {"streak": 0},
            "channels": {},
        }
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("state", reason)

    def test_validate_high_streak_fails(self):
        data = {
            "states": {"state": "healthy"},
            "streaks": {"streak": 3},
            "channels": {},
        }
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("streak", reason)

    def test_validate_invalid_state_fails(self):
        data = {
            "states": {"state": "UNKNOWN_GARBAGE"},
            "streaks": {},
            "channels": {},
        }
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("state", reason)


if __name__ == "__main__":
    unittest.main()
