"""
tests/test_checks_recording.py - RecordingCheck 단위 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.recording import RecordingCheck


CONFIG = {"recording": {"expected_channels": 4, "session_progress": "4/4"}}
CONFIG_NO_PROGRESS = {"recording": {"expected_channels": 4}}


class TestRecordingCheckCollect(unittest.TestCase):
    def setUp(self):
        self.check = RecordingCheck()

    def test_collect_gets_progress(self):
        """'4/4' 포함 로그 → progress='4/4' 반환"""
        ssh = MagicMock()
        ssh.run.return_value = "Apr 02 10:00:01 target gstApp[1234]: recording progress 4/4 channels active"

        data = self.check.collect(ssh, CONFIG)

        self.assertEqual(data["progress"], "4/4")

    def test_collect_no_output(self):
        """ssh.run() → None 반환 시 progress=None 반환"""
        ssh = MagicMock()
        ssh.run.return_value = None

        data = self.check.collect(ssh, CONFIG)

        self.assertIsNone(data["progress"])


class TestRecordingCheckValidate(unittest.TestCase):
    def setUp(self):
        self.check = RecordingCheck()

    def test_validate_correct_progress_passes(self):
        """actual='4/4', expected='4/4' → (True, 'OK')"""
        data = {"raw_output": "progress 4/4", "progress": "4/4"}
        passed, reason = self.check.validate(data, CONFIG)
        self.assertTrue(passed)
        self.assertEqual(reason, "OK")

    def test_validate_wrong_progress_fails(self):
        """actual='2/4', expected='4/4' → (False, 불일치 메시지)"""
        data = {"raw_output": "progress 2/4", "progress": "2/4"}
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("2/4", reason)
        self.assertIn("4/4", reason)

    def test_validate_null_config_skips(self):
        """session_progress 미설정 → (True, 'Skipped...')"""
        data = {"raw_output": "", "progress": None}
        passed, reason = self.check.validate(data, CONFIG_NO_PROGRESS)
        self.assertTrue(passed)
        self.assertIn("Skipped", reason)


if __name__ == "__main__":
    unittest.main()
