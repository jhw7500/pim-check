"""
tests/test_junit_reporter.py - JUnit XML 리포터 테스트
"""
from __future__ import annotations

import os
import unittest

from junit_reporter import generate_junit_xml, save_junit_xml


def _make_results():
    return [
        {"name": "process", "passed": True, "reason": "OK", "data": {}, "duration_ms": 100},
        {"name": "thermal", "passed": False, "reason": "Temperature 95 > max 93", "data": {}, "duration_ms": 50},
        {"name": "cam_state", "passed": False, "reason": "known", "data": {},
         "duration_ms": 30, "known_issue": "HW cooling"},
    ]


class TestGenerateJunitXml(unittest.TestCase):
    def test_valid_xml_output(self):
        xml = generate_junit_xml(_make_results(), "test_case", "192.168.0.5")
        self.assertIn('<?xml version="1.0"', xml)
        self.assertIn('name="pim-check.test_case"', xml)
        self.assertIn('tests="3"', xml)
        self.assertIn('failures="2"', xml)

    def test_pass_result_no_failure_element(self):
        results = [{"name": "ok_check", "passed": True, "reason": "OK", "data": {}, "duration_ms": 10}]
        xml = generate_junit_xml(results, "pass_case")
        self.assertNotIn("<failure", xml)

    def test_fail_result_has_failure_element(self):
        results = [{"name": "bad", "passed": False, "reason": "broken", "data": {}, "duration_ms": 10}]
        xml = generate_junit_xml(results, "fail_case")
        self.assertIn("<failure", xml)
        self.assertIn("broken", xml)

    def test_known_issue_has_skipped_element(self):
        results = [{"name": "warn", "passed": False, "reason": "temp", "data": {},
                     "duration_ms": 10, "known_issue": "HW issue"}]
        xml = generate_junit_xml(results, "warn_case")
        self.assertIn("<skipped", xml)
        self.assertIn("HW issue", xml)

    def test_none_case_name_uses_healthcheck(self):
        xml = generate_junit_xml([], None)
        self.assertIn("healthcheck", xml)

    def test_duration_calculation(self):
        results = [{"name": "a", "passed": True, "reason": "OK", "data": {}, "duration_ms": 1500}]
        xml = generate_junit_xml(results, "dur")
        self.assertIn('time="1.5"', xml)


class TestSaveJunitXml(unittest.TestCase):
    def test_save_creates_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = save_junit_xml(_make_results(), "save_test", "host", 1, 1, tmpdir)
            self.assertTrue(os.path.exists(filepath))
            self.assertTrue(filepath.endswith(".xml"))
            with open(filepath) as f:
                content = f.read()
            self.assertIn("<?xml", content)


if __name__ == "__main__":
    unittest.main()
