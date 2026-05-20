"""
tests/test_gaps.py - 테스트 갭 보완: 누락된 단위/통합 테스트
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from config import load_profile
from engine import Engine
from checks.custom import CustomCommandCheck

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "profiles")


# === 단위 테스트 ===

class TestMergeSnapshotsEdgeCases(unittest.TestCase):
    def test_empty_snapshots_returns_empty(self):
        """빈 스냅샷 리스트 → 빈 결과"""
        ssh = MagicMock()
        engine = Engine(ssh, {"monitor": {"duration_sec": 0, "interval_sec": 5}, "checks": {}})
        result = engine.merge_snapshots([])
        self.assertEqual(result, [])

    def test_single_snapshot_preserved(self):
        """스냅샷 1개 → 그대로 반환"""
        ssh = MagicMock()
        engine = Engine(ssh, {"monitor": {"duration_sec": 0, "interval_sec": 5}, "checks": {}})
        snapshot = [{"name": "thermal", "passed": True, "reason": "OK", "data": {}}]
        result = engine.merge_snapshots([snapshot])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["passed"])


class TestSetupBackupFailure(unittest.TestCase):
    @patch("setup.time.sleep")
    def test_backup_failure_aborts_setup(self, mock_sleep):
        """backup() 실패 시 apply_changes 호출 안 됨"""
        from setup import SetupManager
        ssh = MagicMock()

        def side_effect(cmd, **kwargs):
            if "jq " in cmd and "=" in cmd and "> /tmp" in cmd:
                # apply_changes의 jq 쓰기 명령 — 이건 호출되면 안 됨
                raise AssertionError("apply_changes should not be called after backup failure")
            if "cp " in cmd and "echo OK" in cmd:
                return None  # backup 실패
            if "jq " in cmd:
                return "1920"  # check_current 읽기
            return None

        ssh.run.side_effect = side_effect
        mgr = SetupManager(ssh, reboot_timeout=10, poll_interval=2)

        # AssertionError가 발생하지 않아야 함 (apply 호출 안 됨)
        mgr.run_setup({
            "edgeconf_changes": {".VHL_CAM.cam_width": 1280},
            "reboot_after": False,
        })


class TestCustomCheckNoExpected(unittest.TestCase):
    def test_no_expected_command_fails_on_none_output(self):
        """expected/expected_min 둘 다 없고 output=None → FAIL"""
        check = CustomCommandCheck()
        data = {"results": [
            {"name": "health", "output": None, "expected": None,
             "expected_min": None, "on_fail": "health check failed"},
        ], "skipped": False}
        passed, reason = check.validate(data, {})
        self.assertFalse(passed)
        self.assertIn("health check failed", reason)

    def test_no_expected_command_passes_with_output(self):
        """expected/expected_min 둘 다 없지만 output 있으면 → PASS"""
        check = CustomCommandCheck()
        data = {"results": [
            {"name": "health", "output": "some output", "expected": None,
             "expected_min": None, "on_fail": "health check failed"},
        ], "skipped": False}
        passed, reason = check.validate(data, {})
        self.assertTrue(passed)


class TestRunCaseSshConnectFail(unittest.TestCase):
    @patch("pim_check.Reporter")
    @patch("pim_check.Engine")
    @patch("pim_check.load_profile")
    @patch("pim_check.SshClient")
    def test_ssh_connect_fail_returns_1(self, MockSsh, mock_load, MockEngine, MockReporter):
        """SSH 연결 실패 시 exit code 1 반환"""
        import pim_check
        mock_load.return_value = {
            "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
            "monitor": {"duration_sec": 0, "interval_sec": 5},
            "checks": {},
        }
        MockSsh.return_value.check_connectivity.return_value = False

        ret = pim_check.run_case(None, "192.168.0.5", None, None, 0)
        self.assertEqual(ret, 1)
        MockEngine.assert_not_called()


class TestRunAllWorstExit(unittest.TestCase):
    @patch("pim_check.run_case")
    @patch("pim_check.list_cases")
    def test_all_returns_worst_exit(self, mock_list, mock_run):
        """--all 모드에서 하나라도 실패하면 exit 1"""
        import pim_check
        mock_list.return_value = ["720p_2ch", "720p_4ch"]
        mock_run.side_effect = [0, 1]  # 첫번째 PASS, 두번째 FAIL

        ret = pim_check.main(["--all"])
        self.assertEqual(ret, 1)

    @patch("pim_check.run_case")
    @patch("pim_check.list_cases")
    def test_all_returns_0_when_all_pass(self, mock_list, mock_run):
        """--all 모드에서 모두 성공하면 exit 0"""
        import pim_check
        mock_list.return_value = ["720p_2ch", "720p_4ch"]
        mock_run.return_value = 0

        ret = pim_check.main(["--all"])
        self.assertEqual(ret, 0)


# === 통합 테스트 ===

class TestAllCasesYamlLoadable(unittest.TestCase):
    def test_all_yaml_profiles_load_without_error(self):
        """profiles/cases/ 의 모든 YAML이 base.yaml과 정상 머지됨"""
        cases_dir = os.path.join(PROFILES_DIR, "cases")
        for fname in os.listdir(cases_dir):
            if not fname.endswith(".yaml"):
                continue
            case_name = fname.replace(".yaml", "")
            with self.subTest(case=case_name):
                profile = load_profile(PROFILES_DIR, case=case_name)
                self.assertIn("target", profile)
                self.assertIn("checks", profile)
                self.assertIn("monitor", profile)


class TestFullCaseFlow(unittest.TestCase):
    @patch("setup.time.sleep")
    @patch("pim_check.SshClient")
    @patch("pim_check.load_profile")
    def test_setup_monitor_teardown_flow(self, mock_load, MockSsh, mock_sleep):
        """setup → monitor → teardown 전체 흐름 (mock)"""
        import pim_check

        mock_load.return_value = {
            "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
            "monitor": {"duration_sec": 0, "interval_sec": 5},
            "checks": {},
            "setup": {
                "edgeconf_changes": {".VHL_CAM.cam_width": 1920},
                "reboot_after": False,
            },
        }
        ssh_inst = MockSsh.return_value
        ssh_inst.check_connectivity.return_value = True
        ssh_inst.preflight_check.return_value = []

        applied = {"cam_width": False}

        def smart_mock(cmd, **kwargs):
            if "jq " in cmd and "> /tmp" in cmd:
                applied["cam_width"] = True  # apply_changes 쓰기 명령 실행됨
                return None
            if "jq " in cmd and "> /tmp" not in cmd:
                # apply 전: 현재값 다름(setup 트리거) / apply 후: read-back verify 통과
                return "1920" if applied["cam_width"] else "1280"
            if "cp " in cmd:
                return "OK"  # backup 성공
            return None

        ssh_inst.run.side_effect = smart_mock

        pim_check.run_case("fhd_4ch", "192.168.0.5", None, None, 0)
        calls = [str(c) for c in ssh_inst.run.call_args_list]
        self.assertTrue(any("cp " in c and "edgeconf" in c for c in calls))


class TestMonitorSshErrorRecovery(unittest.TestCase):
    @patch("engine.time.sleep")
    def test_ssh_error_during_collect_recorded(self, mock_sleep):
        """모니터 루프 중 SSH 에러 → SSH_ERROR 기록"""
        from ssh import SshConnectionError

        ssh = MagicMock()
        ssh.check_connectivity.return_value = True
        ssh.run.side_effect = SshConnectionError("dropped")

        profile = {
            "monitor": {"duration_sec": 1, "interval_sec": 1},
            "checks": {"processes": {"required": ["gstApp"], "optional": []},
                       "cpu": {"bg_check_max_pct": 3.0, "gst_range": [0, 100]}},
        }
        engine = Engine(ssh, profile)
        results = engine.run_snapshot()

        ssh_errors = [r for r in results if "SSH_ERROR" in r["reason"]]
        self.assertTrue(len(ssh_errors) > 0)


if __name__ == "__main__":
    unittest.main()
