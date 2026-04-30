"""
tests/test_plan_gate.py — plan.evaluate_gate + plan.load_baseline 단위 테스트.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

from plan import (
    BASELINE_TTL_DAYS,
    CaseExecution,
    DEFAULT_EXECUTION,
    DEFAULT_GATE,
    Plan,
    evaluate_gate,
    load_baseline,
)


def _plan(gate_overrides: dict | None = None) -> Plan:
    gate = dict(DEFAULT_GATE)
    if gate_overrides:
        gate.update(gate_overrides)
    return Plan(
        name="test", description="test", version=1,
        cases={"regression": ["x"]},
        execution=dict(DEFAULT_EXECUTION),
        gate=gate, reports=[],
    )


def _exe(case_name: str, passed: bool, results: list | None = None,
         section: str = "regression") -> CaseExecution:
    return CaseExecution(
        section=section, case_name=case_name,
        results=results or [{"name": "x", "passed": passed,
                              "reason": "OK" if passed else "F",
                              "data": {}, "duration_ms": 1}],
        passed=passed, retries_used=0, error=None, duration_sec=1.0,
    )


class TestEvaluateGate(unittest.TestCase):

    def test_all_pass_no_baseline(self):
        plan = _plan()
        execs = [_exe("a", True), _exe("b", True)]
        gate = evaluate_gate(plan, execs)
        self.assertEqual(gate.verdict, "PASS")
        self.assertEqual(gate.pass_rate, 1.0)
        self.assertEqual(gate.regressions, [])
        self.assertEqual(gate.new_cases, [])

    def test_threshold_failure(self):
        plan = _plan({"threshold_pass_rate": 1.0})
        execs = [_exe("a", True), _exe("b", False)]
        gate = evaluate_gate(plan, execs)
        self.assertEqual(gate.verdict, "FAIL")
        self.assertEqual(gate.pass_rate, 0.5)

    def test_threshold_relaxed_passes(self):
        plan = _plan({"threshold_pass_rate": 0.5})
        execs = [_exe("a", True), _exe("b", False)]
        gate = evaluate_gate(plan, execs)
        self.assertEqual(gate.verdict, "PASS")

    def test_known_issue_emits_warn(self):
        """case는 passed지만 results에 known_issue 키 있으면 verdict=WARN."""
        plan = _plan()
        results_with_known = [
            {"name": "thermal", "passed": False, "reason": "HOT 95C",
             "data": {}, "duration_ms": 1, "known_issue": "HW cooling issue"},
        ]
        execs = [_exe("a", True, results_with_known)]
        gate = evaluate_gate(plan, execs)
        self.assertEqual(gate.verdict, "WARN")
        self.assertEqual(len(gate.known_warns), 1)
        self.assertEqual(gate.known_warns[0]["case"], "a")

    def test_baseline_diff_regressions(self):
        plan = _plan({"baseline_ref": {"file": "x.json", "fail_on_new_failure": True}})
        baseline = {"executions": [
            {"case_name": "a", "passed": True},
            {"case_name": "b", "passed": True},
        ]}
        execs = [_exe("a", True), _exe("b", False)]
        gate = evaluate_gate(plan, execs, baseline=baseline)
        self.assertEqual(gate.regressions, ["b"])
        self.assertEqual(gate.fixed, [])
        # fail_on_new_failure이라 verdict=FAIL
        self.assertEqual(gate.verdict, "FAIL")

    def test_baseline_diff_fixed(self):
        plan = _plan()
        baseline = {"executions": [
            {"case_name": "a", "passed": False},
            {"case_name": "b", "passed": True},
        ]}
        execs = [_exe("a", True), _exe("b", True)]
        gate = evaluate_gate(plan, execs, baseline=baseline)
        self.assertEqual(gate.fixed, ["a"])
        self.assertEqual(gate.regressions, [])

    def test_baseline_new_cases(self):
        plan = _plan()
        baseline = {"executions": [{"case_name": "a", "passed": True}]}
        execs = [_exe("a", True), _exe("b", True)]
        gate = evaluate_gate(plan, execs, baseline=baseline)
        self.assertEqual(gate.new_cases, ["b"])

    def test_fail_on_regression_overrides_threshold(self):
        """fail_on_new_failure=true이면 threshold 통과해도 regression 있으면 FAIL."""
        plan = _plan({
            "threshold_pass_rate": 0.5,  # 50%만 요구
            "baseline_ref": {"file": "x.json", "fail_on_new_failure": True},
        })
        baseline = {"executions": [
            {"case_name": "a", "passed": True},
            {"case_name": "b", "passed": True},
        ]}
        execs = [_exe("a", True), _exe("b", False)]  # pass_rate=0.5 통과지만 b는 regression
        gate = evaluate_gate(plan, execs, baseline=baseline)
        self.assertEqual(gate.verdict, "FAIL")

    def test_no_executions_pass_rate_zero(self):
        plan = _plan()
        gate = evaluate_gate(plan, [])
        self.assertEqual(gate.pass_rate, 0.0)

    def test_warn_overrides_pass(self):
        """known_issue 발생 시 PASS → WARN 승격."""
        plan = _plan({"threshold_pass_rate": 1.0})
        results = [
            {"name": "thermal", "passed": False, "reason": "HOT",
             "data": {}, "duration_ms": 1, "known_issue": "OK by ISSUES.md"},
        ]
        execs = [_exe("a", True, results)]  # passed=True
        gate = evaluate_gate(plan, execs)
        self.assertEqual(gate.verdict, "WARN")


class TestLoadBaseline(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_baseline(self, name: str, payload: dict, age_days: int = 0) -> str:
        path = os.path.join(self.tmpdir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w") as f:
            json.dump(payload, f)
        if age_days > 0:
            old = time.time() - age_days * 86400
            os.utime(path, (old, old))
        return path

    def test_no_file_key_returns_warning(self):
        baseline, warn = load_baseline({}, self.tmpdir)
        self.assertIsNone(baseline)
        self.assertIn("NO_FILE_KEY", warn)

    def test_invalid_ref_type(self):
        baseline, warn = load_baseline("not a dict", self.tmpdir)  # type: ignore[arg-type]
        self.assertIsNone(baseline)
        self.assertIn("INVALID_REF", warn)

    def test_missing_file(self):
        baseline, warn = load_baseline({"file": "missing.json"}, self.tmpdir)
        self.assertIsNone(baseline)
        self.assertIn("NO_FILE", warn)

    def test_loads_valid_baseline(self):
        payload = {"plan": {"name": "x"}, "executions": [
            {"case_name": "a", "passed": True}
        ]}
        self._write_baseline("baseline.json", payload)
        baseline, warn = load_baseline({"file": "baseline.json"}, self.tmpdir)
        self.assertIsNotNone(baseline)
        self.assertIsNone(warn)
        self.assertEqual(baseline["executions"][0]["case_name"], "a")

    def test_stale_baseline_returns_warning_but_loads(self):
        payload = {"executions": []}
        self._write_baseline("old.json", payload, age_days=BASELINE_TTL_DAYS + 5)
        baseline, warn = load_baseline({"file": "old.json"}, self.tmpdir)
        self.assertIsNotNone(baseline)
        self.assertIn("STALE", warn)

    def test_parse_error(self):
        path = os.path.join(self.tmpdir, "broken.json")
        with open(path, "w") as f:
            f.write("not valid json {{{ ")
        baseline, warn = load_baseline({"file": "broken.json"}, self.tmpdir)
        self.assertIsNone(baseline)
        self.assertIn("PARSE_ERROR", warn)

    def test_absolute_path(self):
        payload = {"executions": []}
        path = self._write_baseline("abs.json", payload)
        baseline, warn = load_baseline({"file": path}, "/nonexistent")
        self.assertIsNotNone(baseline)


if __name__ == "__main__":
    unittest.main()
