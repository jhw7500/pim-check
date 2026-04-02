"""
tests/test_reporter.py - reporter.py 단위 테스트
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


if __name__ == "__main__":
    unittest.main()
