"""compare.py 테스트"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from compare import compare_runs, format_comparison


class TestCompareRuns(unittest.TestCase):
    def _make_history(self, tmpdir, entries):
        filepath = os.path.join(tmpdir, "history.jsonl")
        with open(filepath, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_not_enough_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_history(tmpdir, [])
            result = compare_runs(tmpdir)
            self.assertIn("Not enough", result["summary"])

    def test_single_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_history(tmpdir, [
                {"timestamp": "2026-01-01", "case": "test", "result": "PASS",
                 "checks": {"a": True}},
            ])
            result = compare_runs(tmpdir)
            self.assertIn("Not enough", result["summary"])

    def test_pass_to_fail_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_history(tmpdir, [
                {"timestamp": "2026-01-01", "case": "test", "result": "PASS",
                 "checks": {"cam": True, "thermal": True}},
                {"timestamp": "2026-01-02", "case": "test", "result": "FAIL",
                 "checks": {"cam": True, "thermal": False}},
            ])
            result = compare_runs(tmpdir)
            self.assertEqual(len(result["regressed"]), 1)
            self.assertEqual(result["regressed"][0]["check"], "thermal")
            self.assertEqual(result["regressed"][0]["change"], "PASS → FAIL")

    def test_fail_to_pass_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_history(tmpdir, [
                {"timestamp": "2026-01-01", "case": "test", "result": "FAIL",
                 "checks": {"cam": False}},
                {"timestamp": "2026-01-02", "case": "test", "result": "PASS",
                 "checks": {"cam": True}},
            ])
            result = compare_runs(tmpdir)
            self.assertEqual(len(result["improved"]), 1)
            self.assertEqual(result["improved"][0]["change"], "FAIL → PASS")

    def test_new_check_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_history(tmpdir, [
                {"timestamp": "2026-01-01", "checks": {"cam": True}},
                {"timestamp": "2026-01-02", "checks": {"cam": True, "new_check": True}},
            ])
            result = compare_runs(tmpdir)
            new = [r for r in result["improved"] if r["check"] == "new_check"]
            self.assertEqual(len(new), 1)

    def test_case_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_history(tmpdir, [
                {"timestamp": "2026-01-01", "case": "a", "checks": {"x": True}},
                {"timestamp": "2026-01-02", "case": "b", "checks": {"x": False}},
                {"timestamp": "2026-01-03", "case": "a", "checks": {"x": False}},
            ])
            result = compare_runs(tmpdir, case_filter="a")
            self.assertEqual(len(result["regressed"]), 1)


class TestFormatComparison(unittest.TestCase):
    def test_format_output(self):
        result = {
            "improved": [{"check": "cam", "change": "FAIL → PASS"}],
            "regressed": [{"check": "thermal", "change": "PASS → FAIL"}],
            "unchanged": [],
            "summary": "test summary",
        }
        output = format_comparison(result)
        self.assertIn("REGRESSED", output)
        self.assertIn("IMPROVED", output)
        self.assertIn("thermal", output)


if __name__ == "__main__":
    unittest.main()
