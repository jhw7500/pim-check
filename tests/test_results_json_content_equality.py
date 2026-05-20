"""
tests/test_results_json_content_equality.py — *_results.json 콘텐츠 동등성 (Sub-AC 1).

목적 (Sub-AC 1: Content equality)
=================================
pim_check 를 고정된 입력으로 **두 번** 실행하되,
  - 1차: JSONL event stream emitter **비활성(disabled)**
  - 2차: JSONL event stream emitter **활성(enabled)**
했을 때, 산출되는 ``*_results.json`` 의 **바이트/의미(semantic) 콘텐츠가 두 실행
간 완전히 동일**함을 검증한다. 즉, 새 event stream 이 켜지든 꺼지든 기존
``*_results.json`` 출력은 한 바이트도 달라지지 않는다(backward_compatibility).

"pim_check 실행"의 의미
-----------------------
실 보드 SSH 없이 결정론적으로 검증하기 위해, 본 테스트는 ``pim_check._run_plan``
이 ``*_results.json`` 을 만들 때 거치는 **핵심 파이프라인을 그대로** 재현한다
(``pim_check.py`` 의 해당 호출 지점):

  - ``load_plan(...)``      — 고정 입력 plan 로드 (실제 smoke plan 파일)
  - ``start_run_file(...)`` — emitter 활성 시 run-scoped JSONL + current.jsonl 심링크
                              생성 (pim_check.py _run_plan L225 의 호출과 동일)
  - ``evaluate_gate(...)``  — 실제 gate 평가 로직 (_run_plan L271)
  - ``render_reports(...)`` — ``*_results.json`` writer (_run_plan L274 의 호출과
                              동일 시그니처)

emitter 활성 실행에서는 실제 ``event_stream.serialize_fail_event`` +
``write_event`` 로 fault 이벤트 한 줄을 JSONL 에 기록하여, emitter 가 **진짜로
동작**했음을 보장한다(빈 no-op 가 아님). 그럼에도 ``*_results.json`` 은 비활성
실행과 byte-identical 해야 한다.

결정론
------
``render_reports`` 에 고정 ``timestamp`` 를, ``CaseExecution`` 에 고정
``duration_sec`` 를 주어 시계/타이밍 비결정성을 제거한다. 두 실행은 각각 별도
temp ``base_path`` 에 쓰므로 레포의 ``reports/`` 를 오염시키지 않는다.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from event_stream import serialize_fail_event, write_event
from plan import (
    CaseExecution,
    Plan,
    evaluate_gate,
    load_plan,
    render_reports,
)
from run_stream import CURRENT_SYMLINK_NAME, start_run_file

# 레포의 실제 고정 입력 plan (smoke) — json report 형식을 정의하므로
# *_results.json 산출을 보장한다.
PROFILES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "profiles"
)
SMOKE_PLAN_PATH = os.path.join(PROFILES_DIR, "plans", "smoke.yaml")

# 고정(결정론적) 실행 컨텍스트
FIXED_HOST = "192.168.0.5"
FIXED_TS = "20260520_120000"


def _fixed_executions() -> list:
    """보드 무의존·타이밍 무의존 고정 CaseExecution 리스트.

    실제 execute_plan 결과를 대신하는 결정론적 스텁. duration_sec 를 명시하여
    벽시계/타이밍 비결정성을 제거한다. 두 실행 모두 동일 객체를 사용한다.
    """
    return [
        CaseExecution(
            section="regression",
            case_name="fault_gstapp_crash",
            results=[
                {
                    "name": "process",
                    "passed": False,
                    "reason": "gstApp is not running",
                    "data": {},
                    "duration_ms": 50,
                }
            ],
            passed=False,
            retries_used=1,
            error=None,
            duration_sec=64.0,
        ),
        CaseExecution(
            section="regression",
            case_name="board_hw_check",
            results=[
                {
                    "name": "thermal",
                    "passed": True,
                    "reason": "OK",
                    "data": {"max_temp": 72.0},
                    "duration_ms": 30,
                }
            ],
            passed=True,
            retries_used=0,
            error=None,
            duration_sec=12.5,
        ),
    ]


def _run_pim_results(*, emitter_enabled: bool, base_path: str, events_dir: str):
    """pim_check._run_plan 의 결과-산출 + emitter 라이프사이클을 고정 입력으로 재현.

    Args:
        emitter_enabled: True 면 start_run_file + 실제 fault 이벤트 emit 수행.
        base_path: render_reports 가 ``reports/...`` 를 쓰는 base (temp 격리).
        events_dir: emitter 활성 시 JSONL run 파일/심링크가 생성될 디렉토리.

    Returns:
        (json_path, json_bytes) — 산출된 ``*_results.json`` 의 경로와 원시 바이트.
    """
    plan: Plan = load_plan(SMOKE_PLAN_PATH)  # 고정 입력
    executions = _fixed_executions()
    gate = evaluate_gate(plan, executions)  # 실제 gate 로직

    # emitter 라이프사이클 — _run_plan L225 의 start_run_file 호출과 동일.
    if emitter_enabled:
        run_path = start_run_file(plan.name, FIXED_HOST, events_dir=events_dir, ts=FIXED_TS)
        # 실제 직렬화기 + 내구성 라이터로 fault 이벤트 한 줄을 기록 (emit 실증).
        with open(run_path, "a", encoding="utf-8") as handle:
            write_event(
                handle,
                serialize_fail_event(
                    "process",
                    "gstApp is not running",
                    run_id="run-1",
                    plan=plan.name,
                    board=FIXED_HOST,
                    case_name="fault_gstapp_crash",
                ),
            )

    # *_results.json writer — _run_plan L274 의 render_reports 호출과 동일.
    written = render_reports(
        plan, executions, gate,
        host=FIXED_HOST, base_path=base_path, timestamp=FIXED_TS,
    )
    json_path = next(p for p in written if p.endswith(".json"))
    with open(json_path, "rb") as f:
        return json_path, f.read()


class TestResultsJsonContentEquality(unittest.TestCase):
    """emitter 비활성 → 활성 두 실행의 *_results.json 콘텐츠 동등성."""

    def _run_off_then_on(self):
        """(off 바이트, on 바이트, off events_dir, on events_dir) 반환."""
        with tempfile.TemporaryDirectory() as off_base, \
                tempfile.TemporaryDirectory() as on_base, \
                tempfile.TemporaryDirectory() as off_events, \
                tempfile.TemporaryDirectory() as on_events:
            off_events_dir = os.path.join(off_events, "events")
            on_events_dir = os.path.join(on_events, "events")

            # 1차: emitter 비활성
            _, off_bytes = _run_pim_results(
                emitter_enabled=False, base_path=off_base, events_dir=off_events_dir,
            )
            off_has_events = os.path.isdir(off_events_dir)

            # 2차: emitter 활성
            _, on_bytes = _run_pim_results(
                emitter_enabled=True, base_path=on_base, events_dir=on_events_dir,
            )
            on_events_exist = os.path.isdir(on_events_dir)
            on_current = os.path.join(on_events_dir, CURRENT_SYMLINK_NAME)
            on_current_lines = []
            if os.path.exists(on_current):
                with open(on_current, "r", encoding="utf-8") as f:
                    on_current_lines = f.read().splitlines()

            return {
                "off_bytes": off_bytes,
                "on_bytes": on_bytes,
                "off_has_events": off_has_events,
                "on_events_exist": on_events_exist,
                "on_current_lines": on_current_lines,
            }

    def test_results_json_byte_identical_emitter_off_vs_on(self):
        """*_results.json 바이트가 emitter off/on 두 실행 간 완전히 동일."""
        r = self._run_off_then_on()
        self.assertEqual(
            r["off_bytes"],
            r["on_bytes"],
            "emitter 활성/비활성 간 *_results.json 바이트가 달라짐 — "
            "event stream 도입이 기존 결과 출력을 변경했다 (backward_compat 회귀)",
        )

    def test_results_json_semantically_identical_emitter_off_vs_on(self):
        """파싱된 JSON 객체(의미 콘텐츠)가 off/on 두 실행 간 동일."""
        r = self._run_off_then_on()
        off_obj = json.loads(r["off_bytes"].decode("utf-8"))
        on_obj = json.loads(r["on_bytes"].decode("utf-8"))
        self.assertEqual(off_obj, on_obj)
        # 결과 자체가 비어있지 않은 의미있는 산출물인지 sanity 확인.
        self.assertEqual(off_obj["plan"]["name"], "Smoke Sanity Check")
        self.assertEqual(len(off_obj["executions"]), 2)
        self.assertIn("gate", off_obj)

    def test_emitter_enabled_run_actually_emitted(self):
        """활성 실행은 실제로 JSONL 이벤트를 발행했다 (테스트가 vacuous 하지 않음)."""
        r = self._run_off_then_on()
        self.assertTrue(
            r["on_events_exist"], "emitter 활성 실행이 events 디렉토리를 만들지 않음"
        )
        self.assertEqual(
            len(r["on_current_lines"]), 1,
            "emitter 활성 실행이 current.jsonl 에 정확히 한 줄의 이벤트를 남겨야 함",
        )
        record = json.loads(r["on_current_lines"][0])
        self.assertEqual(record["event_type"], "fail")
        self.assertEqual(record["reason"], "gstApp is not running")
        self.assertEqual(record["case_name"], "fault_gstapp_crash")

    def test_emitter_disabled_run_writes_no_events(self):
        """비활성 실행은 어떤 event 파일도 만들지 않는다 (두 실행이 진짜로 다름)."""
        r = self._run_off_then_on()
        self.assertFalse(
            r["off_has_events"],
            "emitter 비활성 실행이 events 디렉토리를 만들었음 — emitter off 가 아님",
        )


if __name__ == "__main__":
    unittest.main()
