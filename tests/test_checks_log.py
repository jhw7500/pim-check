"""
tests/test_checks_log.py - LogCheck 단위 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.log import LogCheck


CONFIG = {"logs": {"error_patterns": ["kernel panic", "Oops", "oom-kill", "CAM_STATE.*invalid"]}}


class TestLogCheckCollect(unittest.TestCase):
    def setUp(self):
        self.check = LogCheck()

    def test_collect_no_errors(self):
        """'No entries' 포함 출력 → matches=[] 반환"""
        ssh = MagicMock()
        ssh.run.return_value = "-- No entries --"

        data = self.check.collect(ssh, CONFIG)

        self.assertEqual(data["matches"], [])

    def test_collect_finds_error(self):
        """'Oops: something bad' 포함 로그 → 1개 매치 반환"""
        ssh = MagicMock()
        ssh.run.return_value = (
            "Apr 02 10:00:01 target kernel: normal message\n"
            "Apr 02 10:00:02 target kernel: Oops: something bad happened\n"
            "Apr 02 10:00:03 target kernel: another normal line\n"
        )

        data = self.check.collect(ssh, CONFIG)

        self.assertEqual(len(data["matches"]), 1)
        self.assertEqual(data["matches"][0]["pattern"], "Oops")
        self.assertIn("Oops", data["matches"][0]["line"])

    def test_collect_handles_ssh_failure(self):
        """ssh.run() → None 반환 시 matches=[] 반환"""
        ssh = MagicMock()
        ssh.run.return_value = None

        data = self.check.collect(ssh, CONFIG)

        self.assertEqual(data["matches"], [])


class TestLogCheckValidate(unittest.TestCase):
    def setUp(self):
        self.check = LogCheck()

    def test_validate_no_matches_passes(self):
        """matches=[] → (True, 'OK')"""
        data = {"matches": []}
        passed, reason = self.check.validate(data, CONFIG)
        self.assertTrue(passed)
        self.assertEqual(reason, "OK")

    def test_validate_matches_fails(self):
        """'kernel panic' 매치 존재 → (False, 에러 메시지)"""
        data = {
            "matches": [
                {"pattern": "kernel panic", "line": "Apr 02 10:00:01 target kernel: kernel panic - not syncing"}
            ]
        }
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("kernel panic", reason)


if __name__ == "__main__":
    unittest.main()
