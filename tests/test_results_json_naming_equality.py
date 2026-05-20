"""
tests/test_results_json_naming_equality.py — *_results.json 명명 동등성 (Sub-AC 3).

목적 (Sub-AC 3: Naming equality)
================================
pim_check 를 **동일한 고정 입력**으로 두 번 실행하되,
  - 1차: JSONL event stream emitter **비활성(disabled)**
  - 2차: JSONL event stream emitter **활성(enabled)**
했을 때, 산출되는 ``*_results.json`` 의 **파일명 명명 규칙** —
즉 (a) base-name(파일명 stem) 유도 결과와 (b) 확장자(suffix) — 이 두 실행 간
**완전히 동일**함을 검증한다.

Sub-AC 2(path equality)는 결과 파일이 **같은 위치(경로)** 에 쓰임을 보장한다.
본 Sub-AC 3 은 그보다 좁고 명시적으로, 결과 파일의 **이름 자체**(stem + suffix)가
emitter on/off 와 무관하게 같은 입력으로부터 같은 규칙으로 유도됨을 보장한다.
event stream emitter 는 결과 파일명을 결정하는 어떤 입력에도 관여하지 않고
별도의 ``events/`` 디렉토리에만 (events/<ts>_<plan>_<board>.jsonl, current.jsonl)
기록하므로, emitter 도입은 ``*_results.json`` 의 base-name 유도나 suffix 를 단 한
글자도 바꿔서는 안 된다(backward_compatibility / separation_of_concerns).

"pim_check 실행"의 의미
-----------------------
실 보드 SSH 없이 결정론적으로 검증하기 위해, 본 테스트는 ``pim_check._run_plan``
이 ``*_results.json`` 을 만들 때 거치는 핵심 명명-결정 파이프라인을 그대로
재현한다:

  - ``load_plan(...)``      — 고정 입력 plan 로드 (실제 smoke plan 파일)
  - ``start_run_file(...)`` — emitter 활성 시에만 호출. run-scoped JSONL + current
                              심링크를 **별도 events_dir** 에 생성 (결과 명명과 무관).
  - ``evaluate_gate(...)``  — 실제 gate 평가 로직
  - ``render_reports(...)`` — ``*_results.json`` writer. 결과 파일명은 오직 plan 의
                              report path 템플릿 + (plan_name, timestamp) 치환으로
                              유도된다 (smoke: ``reports/smoke/{timestamp}.json``).

결정론
------
``render_reports`` 에 고정 ``timestamp`` 를 주어 명명 비결정성을 제거한다. base-name
유도 *규칙* 자체가 emitter 와 무관함을 보이기 위해, 단일 고정값뿐 아니라 서로 다른
timestamp 입력에 대해서도 off/on stem 이 동일하게 유도되는지 함께 검증한다.
"""
from __future__ import annotations

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
ALT_TS = "20991231_235959"  # 명명 유도 *규칙* 검증용 두 번째 입력


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


def _run_pim_results_path(
    *, emitter_enabled: bool, base_path: str, events_dir: str, ts: str = FIXED_TS
) -> str:
    """pim_check._run_plan 의 명명-결정 파이프라인 + emitter 라이프사이클 재현.

    Args:
        emitter_enabled: True 면 별도 events_dir 에 start_run_file 로 실제 run
                         파일/심링크를 만들고 fault 이벤트 한 줄을 emit 한다.
        base_path: render_reports 가 ``reports/...`` 를 쓰는 base.
        events_dir: emitter 활성 시 JSONL run 파일/심링크가 생성될 디렉토리
                    (결과 명명과 분리된 위치).
        ts: render_reports 에 주는 고정 timestamp (파일명 stem 의 입력).

    Returns:
        산출된 ``*_results.json`` 의 절대 경로 (writer 가 반환한 그대로).
    """
    plan: Plan = load_plan(SMOKE_PLAN_PATH)  # 고정 입력
    executions = _fixed_executions()
    gate = evaluate_gate(plan, executions)  # 실제 gate 로직

    # emitter 라이프사이클 — 활성 시에만, 결과 명명과 무관한 events_dir 에 기록.
    if emitter_enabled:
        run_path = start_run_file(
            plan.name, FIXED_HOST, events_dir=events_dir, ts=ts
        )
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

    # *_results.json writer — 파일명은 오직 (plan report 템플릿, plan_name, timestamp).
    written = render_reports(
        plan, executions, gate,
        host=FIXED_HOST, base_path=base_path, timestamp=ts,
    )
    return next(p for p in written if p.endswith(".json"))


class TestResultsJsonNamingEquality(unittest.TestCase):
    """emitter 비활성 → 활성 두 실행의 *_results.json 명명(stem+suffix) 동등성."""

    def _off_on_filenames(self, *, ts: str = FIXED_TS):
        """동일 입력으로 off/on 실행 후 (off basename, on basename) 반환."""
        with tempfile.TemporaryDirectory() as base, \
                tempfile.TemporaryDirectory() as events_root:
            events_dir = os.path.join(events_root, "events")
            off_path = _run_pim_results_path(
                emitter_enabled=False, base_path=base, events_dir=events_dir, ts=ts,
            )
            on_path = _run_pim_results_path(
                emitter_enabled=True, base_path=base, events_dir=events_dir, ts=ts,
            )
            return os.path.basename(off_path), os.path.basename(on_path)

    def test_filename_identical_emitter_off_vs_on(self):
        """*_results.json 전체 파일명이 emitter off/on 간 글자 단위로 동일."""
        off_name, on_name = self._off_on_filenames()
        self.assertEqual(
            off_name,
            on_name,
            "emitter 활성/비활성 간 *_results.json 파일명이 달라짐 — "
            "event stream 도입이 결과 파일 명명을 바꿨다 (naming_equality 회귀)",
        )

    def test_suffix_identical_and_is_json(self):
        """확장자(suffix)가 off/on 간 동일하며 ``.json`` 임."""
        off_name, on_name = self._off_on_filenames()
        off_suffix = os.path.splitext(off_name)[1]
        on_suffix = os.path.splitext(on_name)[1]
        self.assertEqual(
            off_suffix, on_suffix,
            "emitter off/on 간 *_results.json 확장자(suffix)가 달라짐",
        )
        self.assertEqual(
            on_suffix, ".json",
            "결과 파일 확장자가 .json 이 아님 — 명명 규칙 회귀",
        )

    def test_basename_stem_identical(self):
        """base-name(확장자 제외 stem) 유도 결과가 off/on 간 동일."""
        off_name, on_name = self._off_on_filenames()
        off_stem = os.path.splitext(off_name)[0]
        on_stem = os.path.splitext(on_name)[0]
        self.assertEqual(
            off_stem, on_stem,
            "emitter off/on 간 *_results.json base-name(stem)이 달라짐",
        )
        # smoke plan 명세상 stem 은 timestamp 로 유도된다.
        self.assertEqual(
            on_stem, FIXED_TS,
            "결과 파일 stem 이 입력 timestamp 로부터 유도되지 않음 — 명명 규칙 회귀",
        )

    def test_basename_derivation_rule_emitter_independent(self):
        """base-name 유도 *규칙* 자체가 emitter 와 무관함을 두 입력으로 증명.

        단일 고정값이 우연히 같은 게 아니라, 다른 timestamp 입력에 대해서도 off/on
        이 동일 규칙(stem == timestamp)으로 유도됨을 확인한다.
        """
        off_a, on_a = self._off_on_filenames(ts=FIXED_TS)
        off_b, on_b = self._off_on_filenames(ts=ALT_TS)

        # 같은 입력 내 off==on
        self.assertEqual(off_a, on_a)
        self.assertEqual(off_b, on_b)
        # 다른 입력에 대해 stem 이 규칙대로 함께 변한다 (off/on 모두 동일하게).
        self.assertEqual(os.path.splitext(off_a)[0], FIXED_TS)
        self.assertEqual(os.path.splitext(on_a)[0], FIXED_TS)
        self.assertEqual(os.path.splitext(off_b)[0], ALT_TS)
        self.assertEqual(os.path.splitext(on_b)[0], ALT_TS)
        # 입력이 다르면 파일명도 달라야 한다 (명명 규칙이 입력에 실제로 반응함).
        self.assertNotEqual(off_a, off_b)

    def test_filename_has_no_emitter_token_leak(self):
        """결과 파일명에 emitter/event 관련 토큰이 새어들지 않는다 (관심사 분리).

        events/current.jsonl 명명 규칙이 *_results.json 파일명에 영향을 주면 안 된다.
        """
        _, on_name = self._off_on_filenames()
        lowered = on_name.lower()
        for token in ("event", "current", "jsonl", "heartbeat"):
            self.assertNotIn(
                token, lowered,
                f"결과 파일명에 emitter 토큰 '{token}' 이 포함됨 — 명명 관심사 분리 위반",
            )

    def test_emitter_enabled_run_actually_emitted(self):
        """활성 실행은 실제로 events_dir 에 run 파일/심링크를 만들었다 (non-vacuous).

        명명 동등성 비교가 'emitter 가 진짜로 동작한' 실행과의 비교임을 보장한다.
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
