"""
tests/test_plan_reports.py — plan.render_reports 단위 테스트.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from plan import (
    CaseExecution,
    DEFAULT_EXECUTION,
    DEFAULT_GATE,
    GateResult,
    Plan,
    render_reports,
)


def _plan(reports: list[dict]) -> Plan:
    return Plan(
        name="test_plan", description="testing reports", version=1,
        cases={"regression": ["a"]}, execution=dict(DEFAULT_EXECUTION),
        gate=dict(DEFAULT_GATE), reports=reports,
    )


def _exe(case: str, passed: bool, section: str = "regression") -> CaseExecution:
    return CaseExecution(
        section=section, case_name=case,
        results=[{"name": "x", "passed": passed,
                  "reason": "OK" if passed else "FAIL_REASON",
                  "data": {}, "duration_ms": 1}],
        passed=passed, retries_used=0, error=None, duration_sec=2.5,
    )


def _gate(verdict: str = "PASS", pass_rate: float = 1.0,
          regressions: list | None = None) -> GateResult:
    return GateResult(
        verdict=verdict, pass_rate=pass_rate,
        regressions=regressions or [], fixed=[], new_cases=[],
        known_warns=[],
    )


class TestRenderReports(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_json_report_structure(self):
        plan = _plan([{"format": "json", "path": "out/{plan_name}/{timestamp}.json"}])
        execs = [_exe("a", True)]
        gate = _gate()
        written = render_reports(plan, execs, gate, host="1.2.3.4",
                                 base_path=self.tmpdir, timestamp="20260430_120000")
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0].endswith("test_plan/20260430_120000.json"))
        self.assertTrue(os.path.exists(written[0]))
        with open(written[0]) as f:
            payload = json.load(f)
        self.assertEqual(payload["plan"]["name"], "test_plan")
        self.assertEqual(payload["timestamp"], "20260430_120000")
        self.assertEqual(payload["host"], "1.2.3.4")
        self.assertEqual(payload["gate"]["verdict"], "PASS")
        self.assertEqual(len(payload["executions"]), 1)
        self.assertEqual(payload["executions"][0]["case_name"], "a")

    def test_junit_xml_structure(self):
        plan = _plan([{"format": "junit", "path": "out/{timestamp}.xml"}])
        execs = [_exe("a", True), _exe("b", False)]
        gate = _gate(verdict="FAIL", pass_rate=0.5)
        written = render_reports(plan, execs, gate, host="x",
                                 base_path=self.tmpdir, timestamp="20260430_120000")
        tree = ET.parse(written[0])
        root = tree.getroot()
        self.assertEqual(root.tag, "testsuite")
        self.assertEqual(root.get("tests"), "2")
        self.assertEqual(root.get("failures"), "1")
        cases = root.findall("testcase")
        self.assertEqual(len(cases), 2)
        # b는 failure 노드 있음
        self.assertEqual(cases[1].get("name"), "b")
        failures = cases[1].findall("failure")
        self.assertEqual(len(failures), 1)
        self.assertIn("FAIL_REASON", failures[0].text or "")

    def test_html_report_contains_verdict_and_rows(self):
        plan = _plan([{"format": "html", "path": "out/{timestamp}.html"}])
        execs = [_exe("a", True), _exe("b", False)]
        gate = _gate(verdict="FAIL", pass_rate=0.5)
        written = render_reports(plan, execs, gate, host="x",
                                 base_path=self.tmpdir, timestamp="20260430_120000")
        with open(written[0]) as f:
            html = f.read()
        self.assertIn("VERDICT: FAIL", html)
        self.assertIn("test_plan", html)
        self.assertIn("a</td>", html)
        self.assertIn("b</td>", html)

    def test_baseline_diff_section_in_html(self):
        plan = _plan([{"format": "html", "path": "out/h.html"}])
        execs = [_exe("a", False)]
        gate = _gate(verdict="FAIL", pass_rate=0.0, regressions=["a"])
        gate.fixed = ["c"]
        gate.new_cases = ["d"]
        render_reports(plan, execs, gate, host="x",
                       base_path=self.tmpdir, timestamp="20260430_120000")
        with open(os.path.join(self.tmpdir, "out/h.html")) as f:
            html = f.read()
        self.assertIn("Regressions", html)
        self.assertIn("Fixed", html)
        self.assertIn("New cases", html)

    def test_unknown_format_silently_skipped(self):
        plan = _plan([
            {"format": "yaml", "path": "out/x.yaml"},  # not supported
            {"format": "json", "path": "out/x.json"},
        ])
        execs = [_exe("a", True)]
        gate = _gate()
        written = render_reports(plan, execs, gate, host="x",
                                 base_path=self.tmpdir, timestamp="20260430_120000")
        # yaml은 스킵, json만 작성
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0].endswith("x.json"))

    def test_absolute_path_respected(self):
        abs_target = os.path.join(self.tmpdir, "absolute.json")
        plan = _plan([{"format": "json", "path": abs_target}])
        execs = [_exe("a", True)]
        gate = _gate()
        written = render_reports(plan, execs, gate, host="x",
                                 base_path="/nowhere", timestamp="20260430_120000")
        self.assertEqual(written[0], abs_target)
        self.assertTrue(os.path.exists(abs_target))

    def test_timestamp_default_now_ish(self):
        """timestamp=None이면 자동 생성. 형식 YYYYMMDD_HHMMSS."""
        plan = _plan([{"format": "json", "path": "out/{timestamp}.json"}])
        execs = [_exe("a", True)]
        gate = _gate()
        written = render_reports(plan, execs, gate, host="x",
                                 base_path=self.tmpdir, timestamp=None)
        # 파일명에서 timestamp 추출 → 길이 15 (YYYYMMDD_HHMMSS)
        ts_part = os.path.basename(written[0]).replace(".json", "")
        self.assertEqual(len(ts_part), 15)
        self.assertEqual(ts_part[8], "_")


if __name__ == "__main__":
    unittest.main()
