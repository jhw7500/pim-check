"""
tests/test_plan_promote.py — plan.find_latest_report + promote_baseline 단위 테스트.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

from plan import find_latest_report, promote_baseline


def _write(path: str, payload: dict | None = None, mtime_offset: float = 0) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload or {"executions": []}, f)
    if mtime_offset:
        new = time.time() + mtime_offset
        os.utime(path, (new, new))


class TestFindLatestReport(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.plan_name = "comprehensive"
        self.reports_dir = os.path.join(self.tmpdir, "reports", self.plan_name)
        os.makedirs(self.reports_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_returns_none_when_no_dir(self):
        empty = tempfile.mkdtemp()
        try:
            self.assertIsNone(find_latest_report("nonexistent", empty))
        finally:
            shutil.rmtree(empty)

    def test_returns_none_when_no_reports(self):
        self.assertIsNone(find_latest_report(self.plan_name, self.tmpdir))

    def test_returns_latest_by_mtime(self):
        old = os.path.join(self.reports_dir, "20260101_000000.json")
        new = os.path.join(self.reports_dir, "20260430_120000.json")
        _write(old, mtime_offset=-86400)
        _write(new, mtime_offset=0)
        result = find_latest_report(self.plan_name, self.tmpdir)
        self.assertEqual(result, new)

    def test_baselines_subdir_excluded(self):
        """reports/{plan}/baselines/ 안의 파일은 source 후보에서 제외."""
        regular = os.path.join(self.reports_dir, "20260430_120000.json")
        _write(regular)
        baseline = os.path.join(self.reports_dir, "baselines", "v_prev.json")
        _write(baseline)
        result = find_latest_report(self.plan_name, self.tmpdir)
        # baselines/ 하위는 제외 — regular이 반환
        self.assertEqual(result, regular)

    def test_only_json_files(self):
        _write(os.path.join(self.reports_dir, "20260430.json"))
        with open(os.path.join(self.reports_dir, "20260430.html"), "w") as f:
            f.write("<html></html>")
        result = find_latest_report(self.plan_name, self.tmpdir)
        self.assertTrue(result.endswith(".json"))


class TestPromoteBaseline(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.plan_name = "comprehensive"
        self.reports_dir = os.path.join(self.tmpdir, "reports", self.plan_name)
        os.makedirs(self.reports_dir)
        self.source = os.path.join(self.reports_dir, "20260430_120000.json")
        _write(self.source, payload={"plan": {"name": "comprehensive"},
                                      "executions": [{"case_name": "a", "passed": True}]})

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_promote_default_label_uses_source_filename(self):
        target = promote_baseline(self.source, self.plan_name, self.tmpdir)
        expected = os.path.join(self.reports_dir, "baselines", "20260430_120000.json")
        self.assertEqual(target, expected)
        self.assertTrue(os.path.exists(target))
        # 내용 일치 확인
        with open(target) as f:
            data = json.load(f)
        self.assertEqual(data["executions"][0]["case_name"], "a")

    def test_promote_with_label(self):
        target = promote_baseline(self.source, self.plan_name, self.tmpdir,
                                  label="v1_2")
        self.assertTrue(target.endswith("baselines/v1_2.json"))
        self.assertTrue(os.path.exists(target))

    def test_label_with_json_extension_kept(self):
        target = promote_baseline(self.source, self.plan_name, self.tmpdir,
                                  label="v1_2.json")
        self.assertTrue(target.endswith("baselines/v1_2.json"))
        self.assertFalse(target.endswith(".json.json"))

    def test_missing_source_raises(self):
        missing = os.path.join(self.tmpdir, "nope.json")
        with self.assertRaises(FileNotFoundError):
            promote_baseline(missing, self.plan_name, self.tmpdir)

    def test_creates_baselines_dir(self):
        # baselines/ 미리 없어도 자동 생성
        baselines_dir = os.path.join(self.reports_dir, "baselines")
        self.assertFalse(os.path.exists(baselines_dir))
        promote_baseline(self.source, self.plan_name, self.tmpdir, label="x")
        self.assertTrue(os.path.isdir(baselines_dir))

    def test_relative_source_path(self):
        # source를 상대경로로 지정하면 base_path와 결합
        rel = os.path.relpath(self.source, self.tmpdir)
        target = promote_baseline(rel, self.plan_name, self.tmpdir, label="rel_test")
        self.assertTrue(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
