"""tests/test_viewer_render.py - pim_viewer.format_dashboard 순수 렌더 검증.

rich 의존 없이 import 가능한 순수 문자열 렌더러. 진행률/카운트/Fail 목록/현재
case/ETA/케이스 마커/Producer-lost 배너가 모두 표시되는지 확인한다.
"""
from __future__ import annotations

import json

from pim_viewer import format_dashboard
from viewer_state import ViewerState


def _state():
    lines = [
        json.dumps({"event_type": "run_start", "run_id": "r", "plan": "comprehensive",
                    "board": "board-A", "elapsed_s": 0.0,
                    "cases": ["c1", "c2", "c3", "c4"], "total_cases": 4}),
        json.dumps({"event_type": "case_end", "run_id": "r", "plan": "comprehensive",
                    "board": "board-A", "elapsed_s": 3.0, "case_name": "c1",
                    "phase": "validate", "result": "pass", "completed_cases": 1,
                    "pass_count": 1, "fail_count": 0, "avg_case_duration_s": 3.0}),
        json.dumps({"event_type": "case_end", "run_id": "r", "plan": "comprehensive",
                    "board": "board-A", "elapsed_s": 7.0, "case_name": "c2",
                    "phase": "validate", "result": "fail", "completed_cases": 2,
                    "pass_count": 1, "fail_count": 1, "avg_case_duration_s": 3.5,
                    "reason": "gstApp 죽음"}),
        json.dumps({"event_type": "case_start", "run_id": "r", "plan": "comprehensive",
                    "board": "board-A", "elapsed_s": 7.1, "case_name": "c3",
                    "phase": "collect"}),
    ]
    return ViewerState.from_lines(lines)


def test_progress_and_counts_shown():
    out = format_dashboard(_state())
    assert "2/4" in out
    assert "1" in out  # pass/fail counts present
    # Pass/Fail 라벨이 명시된다.
    assert "Pass" in out and "Fail" in out


def test_fail_list_with_reason():
    out = format_dashboard(_state())
    assert "c2" in out
    assert "gstApp 죽음" in out


def test_case_markers_present():
    out = format_dashboard(_state())
    # pass=✓, fail=✗, running/pending=⏳
    assert "✓" in out
    assert "✗" in out
    assert "⏳" in out


def test_current_case_shown():
    out = format_dashboard(_state())
    assert "c3" in out


def _resolved_state():
    lines = [
        json.dumps({"event_type": "run_start", "plan": "smoke", "board": "b",
                    "elapsed_s": 0.0, "cases": ["c1"], "total_cases": 1}),
        json.dumps({"event_type": "case_start", "elapsed_s": 0.1,
                    "case_name": "c1", "phase": "collect"}),
        json.dumps({"event_type": "fail", "elapsed_s": 1.0, "check": "recording",
                    "reason": "NEED_2_FINALIZES", "case_name": "c1"}),
        json.dumps({"event_type": "case_end", "elapsed_s": 9.0, "case_name": "c1",
                    "phase": "validate", "result": "pass", "completed_cases": 1,
                    "pass_count": 1, "fail_count": 0, "avg_case_duration_s": 9.0}),
    ]
    return ViewerState.from_lines(lines)


def test_resolved_fail_not_shown_as_final_failure():
    out = format_dashboard(_resolved_state())
    # 재시도로 회복된 일시 fail 은 최종 실패 섹션이 아니라 회복 섹션에 표시된다.
    assert "Recovered after retry" in out
    assert "↻ c1" in out
    assert "Failures (final):" not in out


def test_active_fault_shown_separately():
    lines = [
        json.dumps({"event_type": "run_start", "plan": "smoke", "board": "b",
                    "elapsed_s": 0.0, "cases": ["c1"], "total_cases": 1}),
        json.dumps({"event_type": "case_start", "elapsed_s": 0.1,
                    "case_name": "c1", "phase": "collect"}),
        json.dumps({"event_type": "fail", "elapsed_s": 1.0, "check": "thermal",
                    "reason": "과열", "case_name": "c1"}),
    ]
    out = format_dashboard(ViewerState.from_lines(lines))
    assert "Faults (in progress):" in out
    assert "⚠ c1" in out


def test_eta_always_shown():
    out = format_dashboard(_state())
    assert "ETA" in out


def test_producer_lost_banner_toggles():
    lost = format_dashboard(_state(), producer_lost=True)
    ok = format_dashboard(_state(), producer_lost=False)
    assert "Producer lost" in lost
    assert "Producer lost" not in ok


def test_handles_empty_state():
    # run_start 전 빈 상태에서도 예외 없이 렌더된다.
    out = format_dashboard(ViewerState())
    assert "ETA" in out and "Pass" in out
