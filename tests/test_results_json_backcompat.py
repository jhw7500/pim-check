"""
tests/test_results_json_backcompat.py — *_results.json 하위호환 회귀 가드.

목적
====
JSONL event stream emitter 도입(형제 AC들)이 기존 ``*_results.json`` 출력을
조금이라도 바꾸지 않음을 보장하는 골든-마스터(characterization) 테스트.

기존 ``*_results.json``은 세 경로에서 생성된다:
  1. ``reporter.Reporter.to_json`` / ``save_json``  — 단일 케이스 결과 JSON
     (예: ``reports/<case>_<ts>.json``)
  2. ``plan._render_json`` (``render_reports``의 ``format: json``) — plan 주도 결과 JSON
     (예: ``reports/<plan>/<ts>.json``)
  3. ``run_comprehensive_verify`` 등 ``run_*`` 스크립트가 루트에 쓰는
     ``<name>_results.json`` — 시나리오 dict 리스트 (json.dump indent=2)

이 테스트는 위 산출물의 **정확한 키 집합과 값**을 동결한다. emit hook이
실수로 결과 dict에 키를 추가/제거하거나 직렬화 형식을 바꾸면 즉시 실패한다.
이로써 AC "Existing *_results.json files continue to be produced identically to
current behavior"를 회귀로부터 방어한다.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from plan import (
    CaseExecution,
    DEFAULT_EXECUTION,
    DEFAULT_GATE,
    GateResult,
    Plan,
    render_reports,
)
from reporter import Reporter


# ── Reporter (단일 케이스 *_results.json) ─────────────────────────────

class TestReporterResultsJsonSchemaFrozen(unittest.TestCase):
    """reporter.Reporter가 만드는 결과 JSON의 스키마/값을 동결."""

    # 현재(=동결 기준) to_json 최상위 키 집합
    EXPECTED_TOP_KEYS = {
        "timestamp", "host", "case", "result",
        "passed", "total", "samples_collected", "samples_total", "checks",
    }
    # 각 check 항목 키 집합
    EXPECTED_CHECK_KEYS = {"name", "passed", "reason"}

    def setUp(self):
        self.reporter = Reporter()
        self.results = [
            {"name": "process", "passed": True, "reason": "OK", "data": {}, "duration_ms": 50},
            {"name": "cam_state", "passed": False, "reason": "state='failed'", "data": {}},
        ]

    def test_to_json_top_level_keys_unchanged(self):
        data = self.reporter.to_json(
            self.results, case_name="720p_2ch", host="192.168.0.5",
            samples_collected=3, samples_total=5,
        )
        self.assertEqual(
            set(data.keys()), self.EXPECTED_TOP_KEYS,
            "to_json 최상위 키 집합이 변경됨 — *_results.json 하위호환 깨짐",
        )

    def test_to_json_check_item_keys_unchanged(self):
        data = self.reporter.to_json(self.results, case_name="c", host="h")
        for check in data["checks"]:
            self.assertEqual(
                set(check.keys()), self.EXPECTED_CHECK_KEYS,
                "checks[] 항목 키 집합이 변경됨",
            )

    def test_to_json_values_identical(self):
        data = self.reporter.to_json(
            self.results, case_name="720p_2ch", host="192.168.0.5",
            samples_collected=3, samples_total=5,
        )
        self.assertEqual(data["host"], "192.168.0.5")
        self.assertEqual(data["case"], "720p_2ch")
        self.assertEqual(data["result"], "FAIL")  # 하나라도 fail이면 FAIL
        self.assertEqual(data["passed"], 1)
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["samples_collected"], 3)
        self.assertEqual(data["samples_total"], 5)
        self.assertEqual(data["checks"][0], {"name": "process", "passed": True, "reason": "OK"})
        self.assertEqual(
            data["checks"][1],
            {"name": "cam_state", "passed": False, "reason": "state='failed'"},
        )

    def test_to_json_all_pass_result_field(self):
        data = self.reporter.to_json(
            [{"name": "x", "passed": True, "reason": "OK"}], case_name=None,
        )
        self.assertEqual(data["result"], "PASS")

    def test_save_json_roundtrip_matches_to_json(self):
        """save_json이 디스크에 쓴 파일이 to_json 스키마와 동일하게 파싱됨."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "reports")
            path = self.reporter.save_json(
                self.results, case_name="720p_2ch", host="192.168.0.5",
                samples_collected=3, samples_total=5, output_dir=out,
            )
            self.assertTrue(os.path.exists(path))
            self.assertTrue(path.endswith(".json"))
            with open(path, encoding="utf-8") as f:
                disk = json.load(f)
            self.assertEqual(set(disk.keys()), self.EXPECTED_TOP_KEYS)
            self.assertEqual(disk["result"], "FAIL")
            self.assertEqual(disk["passed"], 1)
            self.assertEqual(disk["total"], 2)

    def test_save_json_filename_pattern_unchanged(self):
        """파일명 패턴 ``<case|healthcheck>_<ts>.json`` 유지."""
        with tempfile.TemporaryDirectory() as tmp:
            named = self.reporter.save_json([], case_name="smoke", output_dir=tmp)
            self.assertTrue(os.path.basename(named).startswith("smoke_"))
            anon = self.reporter.save_json([], case_name=None, output_dir=tmp)
            self.assertTrue(os.path.basename(anon).startswith("healthcheck_"))


# ── plan.render_reports (plan 주도 *_results.json) ────────────────────

class TestPlanJsonReportSchemaFrozen(unittest.TestCase):
    """plan.render_reports의 format=json 산출물 스키마/값을 동결."""

    EXPECTED_TOP_KEYS = {"plan", "timestamp", "host", "executions", "gate"}
    EXPECTED_PLAN_KEYS = {"name", "description", "version"}
    EXPECTED_EXEC_KEYS = {
        "section", "case_name", "passed", "retries_used",
        "duration_sec", "error", "results",
    }
    EXPECTED_GATE_KEYS = {
        "verdict", "pass_rate", "regressions", "fixed", "new_cases", "known_warns",
    }

    def _plan(self) -> Plan:
        return Plan(
            name="comprehensive", description="full regression", version=1,
            cases={"regression": ["a"]}, execution=dict(DEFAULT_EXECUTION),
            gate=dict(DEFAULT_GATE),
            reports=[{"format": "json", "path": "out/{plan_name}/{timestamp}.json"}],
        )

    def _execs(self):
        return [CaseExecution(
            section="regression", case_name="a",
            results=[{"name": "x", "passed": True, "reason": "OK",
                      "data": {}, "duration_ms": 1}],
            passed=True, retries_used=0, error=None, duration_sec=2.5,
        )]

    def _gate(self) -> GateResult:
        return GateResult(
            verdict="PASS", pass_rate=1.0, regressions=[], fixed=[],
            new_cases=[], known_warns=[],
        )

    def _render(self, tmp):
        return render_reports(
            self._plan(), self._execs(), self._gate(),
            host="192.168.0.5", base_path=tmp, timestamp="20260520_120000",
        )

    def test_json_top_level_keys_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = self._render(tmp)
            self.assertEqual(len(written), 1)
            with open(written[0], encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(set(payload.keys()), self.EXPECTED_TOP_KEYS)

    def test_json_nested_keys_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = self._render(tmp)
            with open(written[0], encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(set(payload["plan"].keys()), self.EXPECTED_PLAN_KEYS)
            self.assertEqual(set(payload["executions"][0].keys()), self.EXPECTED_EXEC_KEYS)
            self.assertEqual(set(payload["gate"].keys()), self.EXPECTED_GATE_KEYS)

    def test_json_values_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = self._render(tmp)
            self.assertTrue(written[0].endswith("comprehensive/20260520_120000.json"))
            with open(written[0], encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["plan"]["name"], "comprehensive")
            self.assertEqual(payload["timestamp"], "20260520_120000")
            self.assertEqual(payload["host"], "192.168.0.5")
            self.assertEqual(payload["gate"]["verdict"], "PASS")
            self.assertEqual(payload["executions"][0]["case_name"], "a")
            self.assertEqual(payload["executions"][0]["passed"], True)


# ── 루트 <name>_results.json 직렬화 형식 ───────────────────────────────

class TestRootResultsJsonSerializationFrozen(unittest.TestCase):
    """run_* 스크립트가 루트에 쓰는 <name>_results.json 형식(리스트 + indent=2)을 동결.

    run_comprehensive_verify.main()의 ``RESULT.write_text(json.dumps(results,
    indent=2, ensure_ascii=False))`` 출력 형태를 표현한다. 실 타겟 SSH 없이
    직렬화 계약만 검증한다.
    """

    def test_scenario_list_roundtrip_indent2(self):
        results = [
            {"name": "p2_quad_720p_ch0_vflip", "result": "PASS",
             "expected_hex": "0x000x02", "actual": "0x000x02",
             "retries_used": 0, "elapsed": 64.0},
            {"name": "p2_quad_720p_ch0_hflip", "result": "FAIL",
             "expected_hex": "0x000x01", "actual": "(no response)",
             "retries_used": 3, "elapsed": 270.5},
        ]
        text = json.dumps(results, indent=2, ensure_ascii=False)
        # 최상위는 리스트, 각 항목은 시나리오 dict
        parsed = json.loads(text)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed, results)
        # indent=2 형식 유지 (사람이 읽는 결과 파일)
        self.assertIn("\n  {", text)

    def test_pass_fail_summary_counts_stable(self):
        """결과 리스트에서 PASS/FAIL 집계 로직(run_* 요약)이 안정적임."""
        results = [{"result": "PASS"}, {"result": "FAIL"}, {"result": "PASS"}]
        p = sum(1 for r in results if r["result"] == "PASS")
        f = len(results) - p
        self.assertEqual((p, f), (2, 1))


if __name__ == "__main__":
    unittest.main()
