"""tests/test_viewer_state.py - ViewerState (JSONL → 화면 상태) 재구성 검증.

핵심 보장: 이벤트를 처음부터 한 번 훑으면 (monotonic replay) 항상 동일한 최종
상태가 나온다 — 재접속 시 "끊긴 적 없는 것처럼" state snapshot 복원의 기반.
"""
from __future__ import annotations

import json

from viewer_state import ViewerState


def _line(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False)


def _stream():
    """run_start → c1 pass → c2 fail → (c3 running) 까지의 이벤트 라인들."""
    return [
        _line({"event_type": "run_start", "ts": "t0", "run_id": "r", "plan": "comprehensive",
               "board": "board-A", "elapsed_s": 0.0,
               "cases": ["c1", "c2", "c3", "c4"], "total_cases": 4}),
        _line({"event_type": "case_start", "ts": "t1", "run_id": "r", "plan": "comprehensive",
               "board": "board-A", "elapsed_s": 0.1, "case_name": "c1", "phase": "collect"}),
        _line({"event_type": "case_end", "ts": "t2", "run_id": "r", "plan": "comprehensive",
               "board": "board-A", "elapsed_s": 3.0, "case_name": "c1", "phase": "validate",
               "result": "pass", "completed_cases": 1, "pass_count": 1, "fail_count": 0,
               "avg_case_duration_s": 3.0}),
        _line({"event_type": "case_start", "ts": "t3", "run_id": "r", "plan": "comprehensive",
               "board": "board-A", "elapsed_s": 3.1, "case_name": "c2", "phase": "collect"}),
        _line({"event_type": "heartbeat", "ts": "t3b", "run_id": "r", "plan": "comprehensive",
               "board": "board-A", "elapsed_s": 5.0, "heartbeat_seq": 1}),
        _line({"event_type": "case_end", "ts": "t4", "run_id": "r", "plan": "comprehensive",
               "board": "board-A", "elapsed_s": 7.0, "case_name": "c2", "phase": "validate",
               "result": "fail", "completed_cases": 2, "pass_count": 1, "fail_count": 1,
               "avg_case_duration_s": 3.5, "reason": "gstApp 죽음"}),
        _line({"event_type": "case_start", "ts": "t5", "run_id": "r", "plan": "comprehensive",
               "board": "board-A", "elapsed_s": 7.1, "case_name": "c3", "phase": "collect"}),
    ]


class TestReplayRestore:
    def test_progress_and_counts(self):
        st = ViewerState.from_lines(_stream())
        assert st.total_cases == 4
        assert st.completed_cases == 2
        assert st.pass_count == 1
        assert st.fail_count == 1
        assert st.progress == (2, 4)

    def test_current_running_case(self):
        st = ViewerState.from_lines(_stream())
        # c3 가 case_start 됐고 아직 case_end 안 됨 → 현재 실행 중.
        assert st.current_case == "c3"

    def test_case_status_markers(self):
        st = ViewerState.from_lines(_stream())
        assert st.case_status == {
            "c1": "pass", "c2": "fail", "c3": "running", "c4": "pending",
        }

    def test_fail_summaries(self):
        st = ViewerState.from_lines(_stream())
        assert st.fail_summaries == {"c2": "gstApp 죽음"}

    def test_eta_avg_times_remaining(self):
        st = ViewerState.from_lines(_stream())
        # avg=3.5, remaining = 4 - 2 = 2 → eta = 7.0
        assert st.eta_seconds == 3.5 * 2

    def test_heartbeat_seq_tracked(self):
        st = ViewerState.from_lines(_stream())
        assert st.last_heartbeat_seq == 1

    def test_not_ended_midrun(self):
        st = ViewerState.from_lines(_stream())
        assert st.run_ended is False


class TestMonotonicConsistency:
    def test_incremental_equals_full_replay(self):
        lines = _stream()
        full = ViewerState.from_lines(lines)
        incr = ViewerState()
        for ln in lines:
            incr.apply(json.loads(ln))
        # 한 번에 replay 한 결과 == 한 줄씩 apply 한 결과.
        assert incr.snapshot() == full.snapshot()

    def test_run_end_sets_final(self):
        lines = _stream() + [_line({
            "event_type": "run_end", "ts": "t9", "run_id": "r", "plan": "comprehensive",
            "board": "board-A", "elapsed_s": 14.0, "completed_cases": 4,
            "pass_count": 3, "fail_count": 1})]
        st = ViewerState.from_lines(lines)
        assert st.run_ended is True
        assert st.completed_cases == 4
        assert st.pass_count == 3
        assert st.fail_count == 1
        # run_end 후에는 진행 중 case 가 없다.
        assert st.current_case is None


class TestRobustness:
    def test_blank_and_malformed_lines_skipped(self):
        lines = ["", "   ", "not json", _stream()[0]]
        st = ViewerState.from_lines(lines)
        # run_start 한 줄만 유효 → total 4, completed 0.
        assert st.total_cases == 4
        assert st.completed_cases == 0

    def test_unknown_event_type_ignored(self):
        st = ViewerState()
        st.apply({"event_type": "future_thing", "elapsed_s": 1.0})
        assert st.completed_cases == 0


class TestRealtimeCheckFail:
    def test_check_fail_surfaces_fault_before_case_end(self):
        st = ViewerState()
        st.apply({"event_type": "run_start", "elapsed_s": 0.0,
                  "cases": ["c1"], "total_cases": 1})
        st.apply({"event_type": "case_start", "elapsed_s": 0.1,
                  "case_name": "c1", "phase": "collect"})
        # 체크 단위 fail — case_end 가 아직 안 왔는데도 fault 가 즉시 보인다.
        st.apply({"event_type": "fail", "elapsed_s": 0.5,
                  "check": "process", "reason": "gstApp 죽음", "case_name": "c1"})
        assert st.fail_summaries == {"c1": "gstApp 죽음"}
        # 카운트는 case_end 전이므로 아직 0 (fail 이벤트가 카운트를 건드리지 않음).
        assert st.completed_cases == 0
        assert st.fail_count == 0
        # case 는 여전히 running.
        assert st.case_status["c1"] == "running"

    def test_fail_without_case_name_is_ignored_for_summary(self):
        st = ViewerState()
        st.apply({"event_type": "fail", "elapsed_s": 0.5,
                  "check": "process", "reason": "x"})
        assert st.fail_summaries == {}
