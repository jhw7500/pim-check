"""html_reporter.py 테스트"""
from __future__ import annotations

import os
import tempfile
import unittest

from html_reporter import generate_html, save_html


class TestGenerateHtml(unittest.TestCase):
    def test_pass_report(self):
        results = [
            {"name": "process", "passed": True, "reason": "OK"},
            {"name": "thermal", "passed": True, "reason": "OK"},
        ]
        html = generate_html(results, "test_case", "192.168.0.5")
        self.assertIn("PASS", html)
        self.assertIn("test_case", html)
        self.assertIn("192.168.0.5", html)
        self.assertIn("2/2", html)

    def test_fail_report(self):
        results = [
            {"name": "process", "passed": True, "reason": "OK"},
            {"name": "thermal", "passed": False, "reason": "temp 92C > max 85C"},
        ]
        html = generate_html(results, "hot_case")
        self.assertIn("FAIL", html)
        self.assertIn("temp 92C", html)

    def test_save_html(self):
        results = [{"name": "check1", "passed": True, "reason": "OK"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_html(results, "mycase", output_dir=tmpdir)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(path.endswith(".html"))
            with open(path) as f:
                content = f.read()
            self.assertIn("<!DOCTYPE html>", content)


if __name__ == "__main__":
    unittest.main()
