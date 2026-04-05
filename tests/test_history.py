"""history.py 테스트"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from history import append_result, read_history


class TestHistory(unittest.TestCase):
    def test_append_and_read(self):
        results = [
            {"name": "process", "passed": True, "reason": "OK"},
            {"name": "thermal", "passed": False, "reason": "too hot"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            append_result(results, "case_a", "192.168.0.5", history_dir=tmpdir)
            append_result(results, "case_b", "192.168.0.5", history_dir=tmpdir)

            entries = read_history(history_dir=tmpdir)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["case"], "case_a")
            self.assertEqual(entries[1]["case"], "case_b")
            self.assertEqual(entries[0]["result"], "FAIL")

    def test_filter_by_case(self):
        results = [{"name": "check", "passed": True, "reason": "OK"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            append_result(results, "case_a", history_dir=tmpdir)
            append_result(results, "case_b", history_dir=tmpdir)
            append_result(results, "case_a", history_dir=tmpdir)

            filtered = read_history(history_dir=tmpdir, case_filter="case_a")
            self.assertEqual(len(filtered), 2)

    def test_empty_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = read_history(history_dir=tmpdir)
            self.assertEqual(entries, [])

    def test_jsonl_format(self):
        results = [{"name": "check", "passed": True, "reason": "OK"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            append_result(results, "case_x", history_dir=tmpdir)
            filepath = os.path.join(tmpdir, "history.jsonl")
            with open(filepath) as f:
                line = f.readline().strip()
            entry = json.loads(line)
            self.assertIn("timestamp", entry)
            self.assertIn("checks", entry)
            self.assertEqual(entry["checks"]["check"], True)


if __name__ == "__main__":
    unittest.main()
