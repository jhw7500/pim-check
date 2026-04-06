"""parallel.py 테스트"""
from __future__ import annotations

import os
import tempfile
import unittest

import yaml

from parallel import load_targets, format_parallel_results


class TestLoadTargets(unittest.TestCase):
    def test_load_from_file(self):
        data = {
            "targets": [
                {"host": "192.168.0.5", "user": "root", "password": "root"},
                {"host": "192.168.0.6", "user": "admin", "password": "admin"},
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name
        try:
            targets = load_targets(path)
            self.assertEqual(len(targets), 2)
            self.assertEqual(targets[0]["host"], "192.168.0.5")
            self.assertEqual(targets[1]["user"], "admin")
        finally:
            os.unlink(path)

    def test_load_missing_file(self):
        targets = load_targets("/nonexistent/targets.yaml")
        self.assertEqual(targets, [])

    def test_load_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            path = f.name
        try:
            targets = load_targets(path)
            self.assertEqual(targets, [])
        finally:
            os.unlink(path)


class TestFormatParallelResults(unittest.TestCase):
    def test_all_pass(self):
        results = [
            {"host": "192.168.0.5", "case": "test", "status": "PASS",
             "results": [{"name": "check", "passed": True}], "collected": 1, "total": 1},
        ]
        output = format_parallel_results(results)
        self.assertIn("[+]", output)
        self.assertIn("1/1 targets OK", output)

    def test_mixed_results(self):
        results = [
            {"host": "192.168.0.5", "case": "test", "status": "PASS",
             "results": [{"name": "check", "passed": True}], "collected": 1, "total": 1},
            {"host": "192.168.0.6", "case": "test", "status": "UNREACHABLE",
             "results": [], "collected": 0, "total": 0},
        ]
        output = format_parallel_results(results)
        self.assertIn("[+]", output)
        self.assertIn("UNREACHABLE", output)
        self.assertIn("1/2 targets OK", output)

    def test_warn_counts_as_ok(self):
        results = [
            {"host": "192.168.0.5", "case": "test", "status": "WARN",
             "results": [{"name": "check", "passed": True}], "collected": 1, "total": 1},
        ]
        output = format_parallel_results(results)
        self.assertIn("1/1 targets OK", output)


class TestRunOnTarget(unittest.TestCase):
    def test_unreachable_target(self):
        from unittest.mock import patch, MagicMock
        from parallel import run_on_target

        with patch("parallel.SshClient") as MockSsh:
            mock_ssh = MagicMock()
            mock_ssh.check_connectivity.return_value = False
            MockSsh.return_value = mock_ssh

            result = run_on_target("192.168.99.99", "root", "root", None, 0)
            self.assertEqual(result["status"], "UNREACHABLE")
            self.assertEqual(result["results"], [])

    def test_setup_failed(self):
        from unittest.mock import patch, MagicMock
        from parallel import run_on_target

        with patch("parallel.SshClient") as MockSsh, \
             patch("parallel.SetupManager") as MockSetup:
            mock_ssh = MagicMock()
            mock_ssh.check_connectivity.return_value = True
            mock_ssh.preflight_check.return_value = []
            MockSsh.return_value = mock_ssh

            mock_mgr = MagicMock()
            mock_mgr.run_setup.side_effect = TimeoutError("reboot timeout")
            MockSetup.return_value = mock_mgr

            result = run_on_target("192.168.0.5", "root", "root", "720p_2ch", 0)
            self.assertEqual(result["status"], "SETUP_FAILED")

    def test_pass_result(self):
        from unittest.mock import patch, MagicMock
        from parallel import run_on_target

        with patch("parallel.SshClient") as MockSsh, \
             patch("parallel.Engine") as MockEngine, \
             patch("parallel.SetupManager"):
            mock_ssh = MagicMock()
            mock_ssh.check_connectivity.return_value = True
            mock_ssh.preflight_check.return_value = []
            MockSsh.return_value = mock_ssh

            mock_engine = MagicMock()
            mock_engine.run_snapshot.return_value = [
                {"name": "check1", "passed": True, "reason": "OK"},
            ]
            MockEngine.return_value = mock_engine

            result = run_on_target("192.168.0.5", "root", "root", None, 0)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(len(result["results"]), 1)

    def test_known_issues_applied(self):
        from unittest.mock import patch, MagicMock
        from parallel import run_on_target

        with patch("parallel.SshClient") as MockSsh, \
             patch("parallel.Engine") as MockEngine, \
             patch("parallel.SetupManager"), \
             patch("parallel.load_profile") as MockProfile:
            mock_ssh = MagicMock()
            mock_ssh.check_connectivity.return_value = True
            mock_ssh.preflight_check.return_value = []
            MockSsh.return_value = mock_ssh

            MockProfile.return_value = {
                "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
                "monitor": {"duration_sec": 0},
                "known_issues": [
                    {"check": "thermal", "reason_contains": "Temperature", "label": "HW issue"}
                ],
            }

            mock_engine = MagicMock()
            mock_engine.run_snapshot.return_value = [
                {"name": "thermal", "passed": False, "reason": "Temperature 90 > max 85"},
            ]
            MockEngine.return_value = mock_engine

            result = run_on_target("192.168.0.5", "root", "root", None, 0)
            self.assertEqual(result["status"], "WARN")
            self.assertIn("known_issue", result["results"][0])


if __name__ == "__main__":
    unittest.main()
