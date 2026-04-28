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

    def test_overrides_merged_into_checks(self):
        """target_overrides가 deep_merge로 profile.checks에 병합 (line 45-46)."""
        from unittest.mock import patch, MagicMock
        from parallel import run_on_target

        with patch("parallel.SshClient") as MockSsh, \
             patch("parallel.Engine") as MockEngine, \
             patch("parallel.SetupManager"), \
             patch("parallel.load_profile") as MockProfile, \
             patch("parallel.deep_merge") as MockMerge:
            mock_ssh = MagicMock()
            mock_ssh.check_connectivity.return_value = True
            mock_ssh.preflight_check.return_value = []
            MockSsh.return_value = mock_ssh

            MockProfile.return_value = {
                "target": {}, "monitor": {"duration_sec": 0},
                "checks": {"cpu": {"max": 80}},
            }
            MockMerge.return_value = {"cpu": {"max": 95}}
            mock_engine = MagicMock()
            mock_engine.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            MockEngine.return_value = mock_engine

            result = run_on_target("192.168.0.5", "root", "root", None, 0,
                                   overrides={"cpu": {"max": 95}})
            MockMerge.assert_called_once()
            self.assertEqual(result["status"], "PASS")

    def test_monitor_mode_uses_run_monitor(self):
        """duration > 0이면 engine.run_monitor 호출 (line 101)."""
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
            mock_engine.run_monitor.return_value = (
                [{"name": "cpu", "passed": True, "reason": "OK"}], 5, 5,
            )
            MockEngine.return_value = mock_engine

            result = run_on_target("192.168.0.5", "root", "root", None, 30)
            mock_engine.run_monitor.assert_called_once()
            mock_engine.run_snapshot.assert_not_called()
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["collected"], 5)

    def test_real_fail_status(self):
        """known_issue 매칭 안된 fail이 있으면 status=FAIL (line 115)."""
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
                {"name": "cpu", "passed": False, "reason": "spike"},
            ]
            MockEngine.return_value = mock_engine

            result = run_on_target("192.168.0.5", "root", "root", None, 0)
            self.assertEqual(result["status"], "FAIL")

    def test_teardown_timeout_swallowed(self):
        """teardown 중 TimeoutError 발생해도 결과 반환되어야 함 (line 127-130)."""
        from unittest.mock import patch, MagicMock
        from parallel import run_on_target

        with patch("parallel.SshClient") as MockSsh, \
             patch("parallel.Engine") as MockEngine, \
             patch("parallel.SetupManager") as MockSetup, \
             patch("parallel.load_profile") as MockProfile:
            mock_ssh = MagicMock()
            mock_ssh.check_connectivity.return_value = True
            mock_ssh.preflight_check.return_value = []
            MockSsh.return_value = mock_ssh

            MockProfile.return_value = {
                "target": {}, "monitor": {"duration_sec": 0},
                "setup": {"reboot_after": True},
            }
            mock_mgr = MagicMock()
            mock_mgr.run_setup.return_value = True  # setup_changed=True
            mock_mgr.run_teardown.side_effect = TimeoutError("teardown reboot timeout")
            MockSetup.return_value = mock_mgr

            mock_engine = MagicMock()
            mock_engine.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            MockEngine.return_value = mock_engine

            # 예외 전파 없이 결과 반환되어야 함
            result = run_on_target("192.168.0.5", "root", "root", "720p_2ch", 0)
            self.assertEqual(result["status"], "PASS")
            mock_mgr.run_teardown.assert_called_once()


class TestRunParallel(unittest.TestCase):
    def test_run_parallel_aggregates_results(self):
        """ThreadPoolExecutor로 여러 호스트 동시 실행 + 결과 집계 (line 147-171)."""
        from unittest.mock import patch
        from parallel import run_parallel

        def fake_run(host, *args, **kwargs):
            return {
                "host": host, "case": None, "status": "PASS",
                "results": [{"name": "x", "passed": True}],
                "collected": 1, "total": 1,
            }

        with patch("parallel.run_on_target", side_effect=fake_run):
            results = run_parallel(
                hosts=["10.0.0.1", "10.0.0.2", "10.0.0.3"],
                case_name=None, max_workers=2,
            )
            self.assertEqual(len(results), 3)
            hosts_seen = {r["host"] for r in results}
            self.assertEqual(hosts_seen, {"10.0.0.1", "10.0.0.2", "10.0.0.3"})
            for r in results:
                self.assertEqual(r["status"], "PASS")

    def test_run_parallel_catches_worker_exception(self):
        """워커에서 예외 발생 시 status=ERROR로 기록 (line 158-168)."""
        from unittest.mock import patch
        from parallel import run_parallel

        def boom(host, *args, **kwargs):
            raise RuntimeError(f"boom-{host}")

        with patch("parallel.run_on_target", side_effect=boom):
            results = run_parallel(hosts=["10.0.0.1"], case_name=None)
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["status"].startswith("ERROR"))
            self.assertIn("boom-10.0.0.1", results[0]["status"])

    def test_run_parallel_with_target_overrides(self):
        """target_overrides가 호스트별로 run_on_target에 전달되는지 (line 152-153)."""
        from unittest.mock import patch
        from parallel import run_on_target  # noqa: F401  -- 사용은 patch에서

        captured = []

        def capture(host, user, password, case, duration, overrides=None):
            captured.append((host, overrides))
            return {"host": host, "case": case, "status": "PASS",
                    "results": [], "collected": 0, "total": 0}

        with patch("parallel.run_on_target", side_effect=capture):
            from parallel import run_parallel
            run_parallel(
                hosts=["a", "b"], case_name=None,
                target_overrides={"a": {"cpu": {"max": 50}}},
            )

        captured_dict = {h: ov for h, ov in captured}
        self.assertEqual(captured_dict["a"], {"cpu": {"max": 50}})
        self.assertIsNone(captured_dict["b"])


class TestFormatExtras(unittest.TestCase):
    """format_parallel_results의 SETUP_FAILED, ERROR 분기 (line 186, 188)."""

    def test_format_setup_failed(self):
        from parallel import format_parallel_results
        results = [
            {"host": "10.0.0.5", "case": "x", "status": "SETUP_FAILED",
             "results": [], "collected": 0, "total": 0},
        ]
        out = format_parallel_results(results)
        self.assertIn("SETUP FAILED", out)
        self.assertIn("0/1 targets OK", out)

    def test_format_error_status(self):
        from parallel import format_parallel_results
        results = [
            {"host": "10.0.0.5", "case": "x", "status": "ERROR: kaboom",
             "results": [], "collected": 0, "total": 0},
        ]
        out = format_parallel_results(results)
        self.assertIn("ERROR: kaboom", out)
        self.assertIn("0/1 targets OK", out)


if __name__ == "__main__":
    unittest.main()
