"""
tests/test_checks_custom.py - CustomCommandCheck 단위 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.custom import CustomCommandCheck


class TestCustomCommandCollect(unittest.TestCase):
    def setUp(self):
        self.check = CustomCommandCheck()

    def test_collect_no_commands_skipped(self):
        ssh = MagicMock()
        data = self.check.collect(ssh, {})
        self.assertTrue(data["skipped"])
        self.assertEqual(data["results"], [])

    def test_collect_runs_commands(self):
        ssh = MagicMock()
        ssh.run.return_value = "OK"
        config = {
            "custom_commands": [
                {"name": "SD writable", "command": "touch /mnt/sd/.test && echo OK",
                 "expected": "OK", "on_fail": "SD read-only"},
            ]
        }
        data = self.check.collect(ssh, config)
        self.assertFalse(data["skipped"])
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["output"], "OK")

    def test_collect_ssh_failure(self):
        ssh = MagicMock()
        ssh.run.return_value = None
        config = {
            "custom_commands": [
                {"name": "test", "command": "echo hi", "expected": "hi"},
            ]
        }
        data = self.check.collect(ssh, config)
        self.assertIsNone(data["results"][0]["output"])


class TestCustomCommandValidate(unittest.TestCase):
    def setUp(self):
        self.check = CustomCommandCheck()

    def test_validate_skipped_passes(self):
        passed, reason = self.check.validate({"results": [], "skipped": True}, {})
        self.assertTrue(passed)
        self.assertIn("Skipped", reason)

    def test_validate_expected_match_passes(self):
        data = {"results": [
            {"name": "SD", "output": "OK", "expected": "OK",
             "expected_min": None, "on_fail": "SD fail"},
        ], "skipped": False}
        passed, reason = self.check.validate(data, {})
        self.assertTrue(passed)

    def test_validate_expected_mismatch_fails(self):
        data = {"results": [
            {"name": "SD", "output": None, "expected": "OK",
             "expected_min": None, "on_fail": "SD read-only"},
        ], "skipped": False}
        passed, reason = self.check.validate(data, {})
        self.assertFalse(passed)
        self.assertIn("SD read-only", reason)

    def test_validate_expected_min_passes(self):
        data = {"results": [
            {"name": "Disk", "output": "2000000", "expected": None,
             "expected_min": 1048576, "on_fail": "Disk full"},
        ], "skipped": False}
        passed, reason = self.check.validate(data, {})
        self.assertTrue(passed)

    def test_validate_expected_min_fails(self):
        data = {"results": [
            {"name": "Disk", "output": "500000", "expected": None,
             "expected_min": 1048576, "on_fail": "Disk nearly full"},
        ], "skipped": False}
        passed, reason = self.check.validate(data, {})
        self.assertFalse(passed)
        self.assertIn("Disk nearly full", reason)

    def test_validate_multiple_commands(self):
        data = {"results": [
            {"name": "SD", "output": "OK", "expected": "OK",
             "expected_min": None, "on_fail": "SD fail"},
            {"name": "Cam", "output": "true", "expected": "false",
             "expected_min": None, "on_fail": "Camera ch0 error"},
        ], "skipped": False}
        passed, reason = self.check.validate(data, {})
        self.assertFalse(passed)
        self.assertIn("Camera ch0 error", reason)
        self.assertNotIn("SD fail", reason)


if __name__ == "__main__":
    unittest.main()
