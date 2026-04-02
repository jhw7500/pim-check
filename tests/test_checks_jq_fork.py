"""
tests/test_checks_jq_fork.py - JqForkCheck 단위 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.jq_fork import JqForkCheck


class TestJqForkCheckCollect(unittest.TestCase):
    def setUp(self):
        self.check = JqForkCheck()
        self.config = {"jq": {"max_forks_per_sample": 2}}

    def test_collect_returns_count(self):
        """pgrep -c jq → '1' 반환 시 count=1 확인"""
        ssh = MagicMock()
        ssh.run.return_value = "1"

        data = self.check.collect(ssh, self.config)

        self.assertEqual(data["count"], 1)

    def test_collect_no_jq_returns_zero(self):
        """pgrep -c jq → None 반환 시 count=0 확인"""
        ssh = MagicMock()
        ssh.run.return_value = None

        data = self.check.collect(ssh, self.config)

        self.assertEqual(data["count"], 0)


class TestJqForkCheckValidate(unittest.TestCase):
    def setUp(self):
        self.check = JqForkCheck()
        self.config = {"jq": {"max_forks_per_sample": 2}}

    def test_validate_within_limit_passes(self):
        """count=1 <= max(2) → True, OK"""
        data = {"count": 1}
        passed, reason = self.check.validate(data, self.config)
        self.assertTrue(passed)
        self.assertEqual(reason, "OK")

    def test_validate_over_limit_fails(self):
        """count=5 > max(2) → False"""
        data = {"count": 5}
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("5", reason)
        self.assertIn("2", reason)


if __name__ == "__main__":
    unittest.main()
