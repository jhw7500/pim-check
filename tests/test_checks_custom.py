"""
tests/test_checks_custom.py - CustomCommandCheck 단위 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.custom import CustomCommandCheck, item_results


class TestItemResults(unittest.TestCase):
    """item_results: 항목별 실측값/통과여부 추출 (뷰어 '측정 vs 기대')."""

    def test_skipped_returns_empty(self):
        self.assertEqual(item_results({"skipped": True}), [])

    def test_expected_exact_match(self):
        out = item_results({"skipped": False, "results": [
            {"name": "ROT", "command": "i2c", "output": "0x000x02",
             "expected": "0x000x02", "expected_min": None, "on_fail": "rot"},
        ]})
        self.assertEqual(out, [{"name": "ROT", "expected": "0x000x02",
                                "actual": "0x000x02", "passed": True}])

    def test_expected_mismatch(self):
        out = item_results({"skipped": False, "results": [
            {"name": "AE", "command": "i2c", "output": "0x020x90",
             "expected": "0x020x99", "expected_min": None, "on_fail": "ae"},
        ]})
        self.assertEqual(out[0]["actual"], "0x020x90")
        self.assertEqual(out[0]["expected"], "0x020x99")
        self.assertFalse(out[0]["passed"])

    def test_expected_min_pass_and_display(self):
        out = item_results({"skipped": False, "results": [
            {"name": "bps", "command": "ffprobe", "output": "8050",
             "expected": None, "expected_min": 8000, "on_fail": "low"},
        ]})
        self.assertEqual(out[0]["expected"], ">= 8000")
        self.assertEqual(out[0]["actual"], "8050")
        self.assertTrue(out[0]["passed"])

    def test_expected_min_fail(self):
        out = item_results({"skipped": False, "results": [
            {"name": "bps", "command": "ffprobe", "output": "5596",
             "expected": None, "expected_min": 8000, "on_fail": "low"},
        ]})
        self.assertFalse(out[0]["passed"])

    def test_expected_min_non_numeric(self):
        out = item_results({"skipped": False, "results": [
            {"name": "bps", "command": "ffprobe", "output": "n/a",
             "expected": None, "expected_min": 8000, "on_fail": "low"},
        ]})
        self.assertFalse(out[0]["passed"])

    def test_non_string_output_does_not_crash(self):
        # output 이 비문자열(int)/None 이어도 AttributeError 없이 처리.
        out = item_results({"skipped": False, "results": [
            {"name": "num", "command": "c", "output": 8050,
             "expected": None, "expected_min": 8000, "on_fail": "x"},
            {"name": "exact", "command": "c", "output": 42,
             "expected": "42", "expected_min": None, "on_fail": "x"},
            {"name": "none", "command": "c", "output": None,
             "expected": "OK", "expected_min": None, "on_fail": "x"},
        ]})
        self.assertTrue(out[0]["passed"])     # 8050 >= 8000
        self.assertTrue(out[1]["passed"])     # str(42) == "42"
        self.assertFalse(out[2]["passed"])    # None != "OK"

    def test_no_expected_uses_nonempty_output(self):
        out = item_results({"skipped": False, "results": [
            {"name": "echo", "command": "echo hi", "output": "hi",
             "expected": None, "expected_min": None, "on_fail": "x"},
            {"name": "empty", "command": "true", "output": None,
             "expected": None, "expected_min": None, "on_fail": "x"},
        ]})
        self.assertTrue(out[0]["passed"])
        self.assertEqual(out[0]["expected"], None)
        self.assertFalse(out[1]["passed"])


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
