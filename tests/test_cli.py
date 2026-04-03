from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# 상위 디렉토리를 sys.path에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pim_check


_MOCK_PROFILE = {
    "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
    "monitor": {"duration_sec": 0, "interval_sec": 5},
    "checks": {},
}


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        args = pim_check.parse_args([])
        self.assertIsNone(args.case)
        self.assertIsNone(args.host)
        self.assertIsNone(args.user)
        self.assertIsNone(args.password)
        self.assertIsNone(args.duration)
        self.assertFalse(args.all)

    def test_user_password_flags(self):
        args = pim_check.parse_args(["--user", "admin", "--password", "secret"])
        self.assertEqual(args.user, "admin")
        self.assertEqual(args.password, "secret")

    def test_case_flag(self):
        args = pim_check.parse_args(["--case", "fhd_4ch"])
        self.assertEqual(args.case, "fhd_4ch")

    def test_host_override(self):
        args = pim_check.parse_args(["--host", "192.168.0.10"])
        self.assertEqual(args.host, "192.168.0.10")

    def test_all_flag(self):
        args = pim_check.parse_args(["--all"])
        self.assertTrue(args.all)

    def test_list_flag(self):
        args = pim_check.parse_args(["--list"])
        self.assertTrue(args.list)

    def test_learn_flag(self):
        args = pim_check.parse_args(["--learn"])
        self.assertTrue(args.learn)


class TestMainFlow(unittest.TestCase):
    @patch("pim_check.Reporter")
    @patch("pim_check.Engine")
    @patch("pim_check.load_profile")
    @patch("pim_check.SshClient")
    def test_basic_healthcheck(self, MockSsh, mock_load, MockEngine, MockReporter):
        mock_load.return_value = _MOCK_PROFILE.copy()
        MockSsh.return_value.check_connectivity.return_value = True
        MockSsh.return_value.preflight_check.return_value = []

        mock_results = [{"name": "cpu", "passed": True, "reason": "OK", "data": {}}]
        MockEngine.return_value.run_snapshot.return_value = mock_results

        MockReporter.return_value.format.return_value = "PASS report"

        # duration=0 → run_snapshot 경로
        ret = pim_check.run_case("healthcheck", "192.168.0.5", None, None, 0)
        self.assertEqual(ret, 0)
        MockEngine.return_value.run_snapshot.assert_called_once()
        MockEngine.return_value.run_monitor.assert_not_called()

    @patch("pim_check.Reporter")
    @patch("pim_check.Engine")
    @patch("pim_check.load_profile")
    @patch("pim_check.SshClient")
    def test_fail_returns_nonzero(self, MockSsh, mock_load, MockEngine, MockReporter):
        mock_load.return_value = _MOCK_PROFILE.copy()
        MockSsh.return_value.check_connectivity.return_value = True
        MockSsh.return_value.preflight_check.return_value = []

        mock_results = [{"name": "cpu", "passed": False, "reason": "too high", "data": {}}]
        MockEngine.return_value.run_snapshot.return_value = mock_results

        MockReporter.return_value.format.return_value = "FAIL report"

        ret = pim_check.run_case("healthcheck", "192.168.0.5", None, None, 0)
        self.assertEqual(ret, 1)


if __name__ == "__main__":
    unittest.main()
