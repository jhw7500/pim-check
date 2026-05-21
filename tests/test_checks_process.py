"""
tests/test_checks_process.py - ProcessCheck 단위 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.process import ProcessCheck


class TestProcessCheckCollect(unittest.TestCase):
    def setUp(self):
        self.check = ProcessCheck()
        self.config = {
            "processes": {
                "required": ["gstApp", "BG_Check_for_pim", "chk_cam_operate"],
                "optional": ["ord", "vcm"],
            },
            "cpu": {
                "bg_check_max_pct": 3.0,
                "gst_range": [0, 100],
            },
        }

    def test_collect_all_running(self):
        """모든 프로세스가 실행 중일 때 running 목록과 CPU 수집 확인"""
        ssh = MagicMock()
        # pgrep은 항상 pid 반환
        ssh.run.side_effect = lambda cmd: "12345" if cmd.startswith("pgrep") else "5.0"

        data = self.check.collect(ssh, self.config)

        all_procs = (
            self.config["processes"]["required"]
            + self.config["processes"]["optional"]
        )
        self.assertEqual(sorted(data["running"]), sorted(all_procs))
        self.assertEqual(data["missing"], [])
        self.assertEqual(len(data["cpu"]), len(all_procs))
        for proc in all_procs:
            self.assertIn(proc, data["cpu"])

    def test_collect_missing_process(self):
        """gstApp에 대해 pgrep이 None 반환 시 missing 목록에 포함 확인"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "pgrep" in cmd and "gstApp" in cmd:
                return None
            if cmd.startswith("pgrep"):
                return "12345"
            # ps 명령 (CPU)
            return "2.5"

        ssh.run.side_effect = side_effect

        data = self.check.collect(ssh, self.config)

        self.assertIn("gstApp", data["missing"])
        self.assertNotIn("gstApp", data["running"])
        self.assertNotIn("gstApp", data["cpu"])

    def test_collect_empty_pgrep_output_is_missing(self):
        """pgrep 이 빈 문자열("")을 반환해도 (None 아님) missing 으로 처리,
        IndexError 없음 — 리부트 직후 프로세스 미기동 윈도우 회귀 방지."""
        ssh = MagicMock()
        ssh.run.return_value = ""   # 매치 없음: exit!=0 + 빈 stdout

        data = self.check.collect(ssh, self.config)

        all_procs = (
            self.config["processes"]["required"]
            + self.config["processes"]["optional"]
        )
        self.assertEqual(sorted(data["missing"]), sorted(all_procs))
        self.assertEqual(data["running"], [])
        self.assertEqual(data["cpu"], {})

    def test_collect_pgrep_x_empty_falls_back_to_pgrep_f(self):
        """pgrep -x 가 "" 면 pgrep -f 폴백을 시도하고, 거기서 pid 가 나오면 running."""
        ssh = MagicMock()

        def side_effect(cmd):
            if cmd.startswith("pgrep -x"):
                return ""          # -x 매치 실패
            if cmd.startswith("pgrep -f"):
                return "4321"      # -f 폴백 성공
            return "3.0"           # ps cpu

        ssh.run.side_effect = side_effect
        data = self.check.collect(ssh, self.config)
        all_procs = (
            self.config["processes"]["required"]
            + self.config["processes"]["optional"]
        )
        self.assertEqual(sorted(data["running"]), sorted(all_procs))
        self.assertEqual(data["missing"], [])


class TestProcessCheckValidate(unittest.TestCase):
    def setUp(self):
        self.check = ProcessCheck()
        self.config = {
            "processes": {
                "required": ["gstApp", "BG_Check_for_pim", "chk_cam_operate"],
                "optional": ["ord", "vcm"],
            },
            "cpu": {
                "bg_check_max_pct": 3.0,
                "gst_range": [0, 100],
            },
        }

    def _make_data(self, missing=None, cpu=None):
        all_procs = (
            self.config["processes"]["required"]
            + self.config["processes"]["optional"]
        )
        missing = missing or []
        running = [p for p in all_procs if p not in missing]
        default_cpu = {p: 1.0 for p in running}
        if cpu:
            default_cpu.update(cpu)
        return {
            "running": running,
            "missing": missing,
            "cpu": default_cpu,
        }

    def test_validate_all_present_passes(self):
        """모든 필수 프로세스 존재 + CPU 범위 내 → PASS"""
        data = self._make_data(cpu={"BG_Check_for_pim": 1.0, "gstApp": 50.0})
        passed, reason = self.check.validate(data, self.config)
        self.assertTrue(passed)
        self.assertEqual(reason, "OK")

    def test_validate_missing_required_fails(self):
        """gstApp이 missing → FAIL"""
        data = self._make_data(missing=["gstApp"])
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("gstApp", reason)

    def test_validate_bg_check_cpu_too_high_fails(self):
        """BG_Check_for_pim CPU 5.0 > 3.0 → FAIL"""
        data = self._make_data(cpu={"BG_Check_for_pim": 5.0, "gstApp": 50.0})
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("BG_Check_for_pim", reason)

    def test_validate_gst_cpu_out_of_range_fails(self):
        """gstApp CPU 150.0 > 100 → FAIL"""
        # gst_range [0, 100] 기준으로 150은 범위 초과
        config = dict(self.config)
        config["cpu"] = {"bg_check_max_pct": 3.0, "gst_range": [10, 95]}
        data = self._make_data(cpu={"BG_Check_for_pim": 1.0, "gstApp": 150.0})
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("gstApp", reason)


if __name__ == "__main__":
    unittest.main()
