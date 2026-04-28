"""
tests/test_reporter.py - reporter.py 단위 테스트
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from reporter import Reporter


def make_result(name: str, passed: bool, reason: str = "OK") -> dict:
    return {"name": name, "passed": passed, "reason": reason, "data": {}}


class TestReporter(unittest.TestCase):

    def setUp(self):
        self.reporter = Reporter()

    def test_all_pass(self):
        results = [
            make_result("cpu_usage", True),
            make_result("memory_usage", True),
        ]
        output = self.reporter.format(results, case_name=None)
        self.assertIn("PASS", output)
        self.assertIn("2/2", output)
        self.assertIn("[+] cpu_usage: PASS", output)
        self.assertIn("[+] memory_usage: PASS", output)

    def test_one_fail(self):
        results = [
            make_result("cpu_usage", True),
            make_result("temperature", False, reason="thermal_zone0=92°C exceeds limit"),
        ]
        output = self.reporter.format(results, case_name=None)
        self.assertIn("FAIL", output)
        self.assertIn("1/2", output)
        self.assertIn("[X] temperature: FAIL", output)
        self.assertIn("thermal_zone0=92°C exceeds limit", output)

    def test_with_case_name(self):
        results = [make_result("cpu_usage", True)]
        output = self.reporter.format(results, case_name="720p_2ch")
        self.assertIn("720p_2ch", output)
        self.assertIn("Case: 720p_2ch", output)

    def test_empty_results(self):
        output = self.reporter.format([], case_name=None)
        self.assertIn("0/0", output)

    def test_samples_displayed(self):
        results = [make_result("cpu_usage", True)]
        output = self.reporter.format(
            results, case_name=None, samples_collected=8, samples_total=10
        )
        self.assertIn("8/10", output)
        self.assertIn("Samples:", output)

    def test_warning_shown(self):
        results = [
            make_result("thermal", False, reason="WARN: temperature 85°C near limit"),
        ]
        output = self.reporter.format(results, case_name=None)
        self.assertIn("WARN", output)
        self.assertIn("WARN: temperature 85°C near limit", output)

    def test_known_issue_demoted_to_warn(self):
        """known_issues 매칭된 FAIL은 WARN으로 표시되며, 다른 모든 체크가 PASS면 전체 status도 WARN."""
        results = [
            make_result("cpu_usage", True),
            make_result("temperature", False, reason="thermal_zone0=92°C exceeds limit"),
        ]
        known_issues = [
            {"check": "temperature", "reason_contains": "thermal_zone0", "label": "TZ0_FLAKY"},
        ]
        output = self.reporter.format(results, case_name=None, known_issues=known_issues)
        self.assertIn("WARN", output)
        self.assertIn("1 known issues", output)
        self.assertIn("[!] temperature: WARN (known: TZ0_FLAKY)", output)

    def test_known_issue_not_matching_remains_fail(self):
        """known_issues 패턴이 reason과 매칭 안 되면 FAIL 유지."""
        results = [
            make_result("temperature", False, reason="actual reason different"),
        ]
        known_issues = [
            {"check": "temperature", "reason_contains": "won't_match", "label": "X"},
        ]
        output = self.reporter.format(results, case_name=None, known_issues=known_issues)
        self.assertIn("FAIL", output)
        self.assertIn("[X] temperature: FAIL", output)

    def test_passed_with_non_ok_reason(self):
        """PASS이지만 reason != 'OK'인 경우 reason 부가 표시 (line 65-66)."""
        results = [make_result("ssh", True, reason="connected in 120ms")]
        output = self.reporter.format(results, case_name=None)
        self.assertIn("[+] ssh: PASS", output)
        self.assertIn("connected in 120ms", output)

    def test_duration_ms_displayed(self):
        """duration_ms 필드가 있으면 출력에 (Nms) 포함."""
        result = make_result("cpu_usage", True)
        result["duration_ms"] = 42
        output = self.reporter.format([result], case_name=None)
        self.assertIn("(42ms)", output)

    def test_to_json_structure(self):
        """to_json이 timestamp/host/case/result/checks 모두 포함하는 dict 반환 (line 84-103)."""
        results = [
            make_result("cpu_usage", True),
            make_result("temperature", False, reason="too hot"),
        ]
        data = self.reporter.to_json(
            results, case_name="720p_2ch", host="192.168.0.5",
            samples_collected=3, samples_total=5,
        )
        self.assertEqual(data["case"], "720p_2ch")
        self.assertEqual(data["host"], "192.168.0.5")
        self.assertEqual(data["result"], "FAIL")
        self.assertEqual(data["passed"], 1)
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["samples_collected"], 3)
        self.assertEqual(data["samples_total"], 5)
        self.assertIn("timestamp", data)
        self.assertEqual(len(data["checks"]), 2)
        self.assertEqual(data["checks"][1]["reason"], "too hot")

    def test_to_json_pass_when_all_passed(self):
        results = [make_result("cpu_usage", True)]
        data = self.reporter.to_json(results, case_name=None)
        self.assertEqual(data["result"], "PASS")

    def test_save_json_creates_file_in_default_dir(self):
        """save_json이 reports/ 디렉토리 자동 생성하고 JSON 파일 저장 (line 115-123)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "reports")
            results = [make_result("cpu_usage", True)]
            filepath = self.reporter.save_json(
                results, case_name="720p_2ch",
                host="192.168.0.5", output_dir=output_dir,
            )
            self.assertTrue(os.path.exists(filepath))
            self.assertTrue(filepath.startswith(output_dir))
            self.assertTrue(filepath.endswith(".json"))
            with open(filepath) as f:
                data = json.load(f)
            self.assertEqual(data["case"], "720p_2ch")
            self.assertEqual(data["result"], "PASS")

    def test_save_json_with_no_case_uses_healthcheck_slug(self):
        """case_name=None일 때 파일명에 'healthcheck' 슬러그 사용 (line 118)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            results = [make_result("cpu_usage", True)]
            filepath = self.reporter.save_json(
                results, case_name=None, output_dir=tmpdir,
            )
            self.assertIn("healthcheck", os.path.basename(filepath))


if __name__ == "__main__":
    unittest.main()
