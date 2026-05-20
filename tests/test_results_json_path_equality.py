"""
tests/test_results_json_path_equality.py — *_results.json 경로 동등성 (Sub-AC 2).

목적 (Sub-AC 2: Path equality)
==============================
pim_check 를 **동일한 고정 입력**으로 두 번 실행하되,
  - 1차: JSONL event stream emitter **비활성(disabled)**
  - 2차: JSONL event stream emitter **활성(enabled)**
했을 때, 산출되는 ``*_results.json`` 파일이 **정확히 같은 파일시스템 경로/위치**
에 기록됨을 검증한다.

Sub-AC 1(콘텐츠 동등성)은 두 실행의 결과 *바이트*가 같음을 보장하고, 본
Sub-AC 2 는 결과가 **같은 자리(경로)** 에 쓰임을 보장한다. event stream emitter
는 결과 파일의 위치를 결정하는 어떤 입력에도 관여하지 않으므로(emitter 는
별도의 ``events/`` 디렉토리에만 기록), emitter on/off 는 ``*_results.json`` 의
경로를 단 한 글자도 바꿔서는 안 된다(backward_compatibility / separation_of_concerns).

"pim_check 실행"의 의미
-----------------------
실 보드 SSH 없이 결정론적으로 검증하기 위해, 본 테스트는 ``pim_check._run_plan``
이 ``*_results.json`` 을 만들 때 거치는 핵심 경로-결정 파이프라인을 그대로
재현한다:

  - ``load_plan(...)``      — 고정 입력 plan 로드 (실제 smoke plan 파일)
  - ``start_run_file(...)`` — emitter 활성 시에만 호출. run-scoped JSONL + current
                              심링크를 **별도 events_dir** 에 생성 (결과 경로와 무관).
  - ``evaluate_gate(...)``  — 실제 gate 평가 로직
  - ``render_reports(...)`` — ``*_results.json`` writer. 결과 경로는 오직
                              (base_path, plan, timestamp) 으로 결정된다.

결정론
------
``render_reports`` 에 고정 ``timestamp`` 를 주어 경로 비결정성을 제거한다. 결과
경로는 ``base_path`` 기준으로 결정되므로, 경로 동등성 검증은 (a) 동일 base_path
로 두 번 실행해 절대경로가 동일한지, (b) 서로 다른 base_path 로 실행해도 base
대비 상대경로가 동일한지 두 측면에서 확인한다.
"""
from __future__ import annotations

import os
import tempfile
import unittest

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
    """보드 무의존·타이밍 무의존 고정 CaseExecution 리스트."""
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


def _run_pim_results_path(*, emitter_enabled: bool, base_path: str, events_dir: str) -> str:
    """pim_check._run_plan 의 경로-결정 파이프라인 + emitter 라이프사이클 재현.

    Args:
        emitter_enabled: True 면 별도 events_dir 에 start_run_file 로 실제 run
                         파일/심링크를 만들고 fault 이벤트 한 줄을 emit 한다.
        base_path: render_reports 가 ``reports/...`` 를 쓰는 base.
        events_dir: emitter 활성 시 JSONL run 파일/심링크가 생성될 디렉토리
                    (결과 경로와 분리된 위치).

    Returns:
        산출된 ``*_results.json`` 의 절대 경로 (writer 가 반환한 그대로).
    """
    plan: Plan = load_plan(SMOKE_PLAN_PATH)  # 고정 입력
    executions = _fixed_executions()
    gate = evaluate_gate(plan, executions)  # 실제 gate 로직

    # emitter 라이프사이클 — 활성 시에만, 결과 경로와 무관한 events_dir 에 기록.
    if emitter_enabled:
        run_path = start_run_file(
            plan.name, FIXED_HOST, events_dir=events_dir, ts=FIXED_TS
        )
        # 실제 직렬화기 + 내구성 라이터로 fault 이벤트 한 줄을 기록 (emit 실증).
        from event_stream import serialize_fail_event, write_event

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

    # *_results.json writer — 결과 경로는 오직 (base_path, plan, timestamp).
    written = render_reports(
        plan, executions, gate,
        host=FIXED_HOST, base_path=base_path, timestamp=FIXED_TS,
    )
    return next(p for p in written if p.endswith(".json"))


class TestResultsJsonPathEquality(unittest.TestCase):
    """emitter 비활성 → 활성 두 실행의 *_results.json 경로 동등성."""

    def test_results_json_same_absolute_path_emitter_off_vs_on(self):
        """동일 base_path 에서 emitter off/on 의 결과 경로가 글자 단위로 동일."""
        with tempfile.TemporaryDirectory() as base, \
                tempfile.TemporaryDirectory() as events_root:
            events_dir = os.path.join(events_root, "events")

            off_path = _run_pim_results_path(
                emitter_enabled=False, base_path=base, events_dir=events_dir,
            )
            on_path = _run_pim_results_path(
                emitter_enabled=True, base_path=base, events_dir=events_dir,
            )

            self.assertEqual(
                off_path,
                on_path,
                "emitter 활성/비활성 간 *_results.json 의 절대 경로가 달라짐 — "
                "event stream 도입이 결과 파일 위치를 바꿨다 (path_equality 회귀)",
            )

    def test_results_json_same_relative_path_across_base_paths(self):
        """서로 다른 base_path 라도 base 대비 상대 경로가 off/on 간 동일.

        결과 경로 결정이 base_path 에만 의존하고 emitter 상태에는 무관함을 증명.
        """
        with tempfile.TemporaryDirectory() as off_base, \
                tempfile.TemporaryDirectory() as on_base, \
                tempfile.TemporaryDirectory() as events_root:
            events_dir = os.path.join(events_root, "events")

            off_path = _run_pim_results_path(
                emitter_enabled=False, base_path=off_base, events_dir=events_dir,
            )
            on_path = _run_pim_results_path(
                emitter_enabled=True, base_path=on_base, events_dir=events_dir,
            )

            off_rel = os.path.relpath(off_path, off_base)
            on_rel = os.path.relpath(on_path, on_base)
            self.assertEqual(
                off_rel,
                on_rel,
                "base 대비 *_results.json 상대 경로가 emitter off/on 간 달라짐",
            )
            # smoke plan 의 reports 명세상 기대 위치 고정 확인.
            self.assertEqual(
                off_rel.replace(os.sep, "/"),
                f"reports/smoke/{FIXED_TS}.json",
            )

    def test_results_json_path_outside_events_dir(self):
        """결과 파일은 emitter 의 events_dir 바깥에 위치한다 (관심사 분리).

        emitter 가 활성이어도 *_results.json 은 events_dir 안으로 새지 않아야 한다.
        """
        with tempfile.TemporaryDirectory() as base, \
                tempfile.TemporaryDirectory() as events_root:
            events_dir = os.path.join(events_root, "events")
            on_path = _run_pim_results_path(
                emitter_enabled=True, base_path=base, events_dir=events_dir,
            )
            real_events = os.path.realpath(events_dir)
            real_result = os.path.realpath(on_path)
            self.assertFalse(
                real_result.startswith(real_events + os.sep),
                "*_results.json 이 emitter 의 events_dir 안에 기록됨 — 관심사 분리 위반",
            )

    def test_emitter_enabled_run_actually_emitted(self):
        """활성 실행은 실제로 events_dir 에 run 파일/심링크를 만들었다 (non-vacuous).

        경로 동등성 비교가 'emitter 가 진짜로 동작한' 실행과의 비교임을 보장한다.
        """
        with tempfile.TemporaryDirectory() as base, \
                tempfile.TemporaryDirectory() as events_root:
            events_dir = os.path.join(events_root, "events")
            _run_pim_results_path(
                emitter_enabled=True, base_path=base, events_dir=events_dir,
            )
            self.assertTrue(
                os.path.isdir(events_dir),
                "emitter 활성 실행이 events 디렉토리를 만들지 않음",
            )
            current = os.path.join(events_dir, CURRENT_SYMLINK_NAME)
            self.assertTrue(
                os.path.exists(current),
                "emitter 활성 실행이 current.jsonl 심링크를 만들지 않음",
            )

    def test_emitter_disabled_run_creates_no_events_dir(self):
        """비활성 실행은 events_dir 를 만들지 않는다 (두 실행이 진짜로 다름)."""
        with tempfile.TemporaryDirectory() as base, \
                tempfile.TemporaryDirectory() as events_root:
            events_dir = os.path.join(events_root, "events")
            _run_pim_results_path(
                emitter_enabled=False, base_path=base, events_dir=events_dir,
            )
            self.assertFalse(
                os.path.isdir(events_dir),
                "emitter 비활성 실행이 events 디렉토리를 만들었음 — emitter off 가 아님",
            )


if __name__ == "__main__":
    unittest.main()
