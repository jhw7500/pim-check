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


class TestFailClassification:
    """check 단위 fail 이벤트를 case 결과로 분류 — 일시(resolved) vs 최종(confirmed)."""

    def _resolved(self):
        # c1: 도중 fail 이벤트가 떴지만 최종 case_end 는 pass (재시도로 회복).
        return ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "fail", "elapsed_s": 0.5, "check": "recording",
                   "reason": "NEED_2_FINALIZES", "case_name": "c1"}),
            _line({"event_type": "case_end", "elapsed_s": 9.0, "case_name": "c1",
                   "phase": "validate", "result": "pass", "completed_cases": 1,
                   "pass_count": 1, "fail_count": 0, "avg_case_duration_s": 9.0}),
        ])

    def test_resolved_when_case_passes_after_fail_event(self):
        st = self._resolved()
        assert st.fail_classification == {"c1": "resolved"}
        # 최종 카운트는 pass — fail_count 0.
        assert st.fail_count == 0
        assert st.case_status["c1"] == "pass"

    def test_confirmed_when_case_ends_fail(self):
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "fail", "elapsed_s": 0.5, "check": "process",
                   "reason": "gstApp 죽음", "case_name": "c1"}),
            _line({"event_type": "case_end", "elapsed_s": 5.0, "case_name": "c1",
                   "phase": "validate", "result": "fail", "completed_cases": 1,
                   "pass_count": 0, "fail_count": 1, "avg_case_duration_s": 5.0,
                   "reason": "gstApp 죽음"}),
        ])
        assert st.fail_classification == {"c1": "confirmed"}

    def test_active_when_fail_event_and_still_running(self):
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "fail", "elapsed_s": 0.5, "check": "thermal",
                   "reason": "과열", "case_name": "c1"}),
        ])
        assert st.fail_classification == {"c1": "active"}
        assert st.case_status["c1"] == "running"

    def test_confirmed_case_end_fail_without_check_event(self):
        # check 단위 fail 이벤트 없이 case_end 만 fail (예: NO_SSH) 도 confirmed.
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "case_end", "elapsed_s": 5.0, "case_name": "c1",
                   "phase": "validate", "result": "fail", "completed_cases": 1,
                   "pass_count": 0, "fail_count": 1, "avg_case_duration_s": 5.0,
                   "reason": "NO_SSH"}),
        ])
        assert st.fail_classification == {"c1": "confirmed"}

    def test_clean_pass_has_no_classification(self):
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "case_end", "elapsed_s": 3.0, "case_name": "c1",
                   "phase": "validate", "result": "pass", "completed_cases": 1,
                   "pass_count": 1, "fail_count": 0, "avg_case_duration_s": 3.0}),
        ])
        assert st.fail_classification == {}


class TestCaseDetails:
    def test_detail_captures_phase_duration_and_fails(self):
        st = TestFailClassification()._resolved()
        det = st.case_details["c1"]
        assert det["status"] == "pass"
        assert det["classification"] == "resolved"
        assert det["phase"] == "validate"
        assert det["duration_s"] == 8.9  # 9.0 - 0.1
        assert det["fail_count"] == 1
        assert det["fails"][0]["check"] == "recording"
        assert det["fails"][0]["reason"] == "NEED_2_FINALIZES"

    def test_detail_for_all_cases_even_without_fails(self):
        st = ViewerState.from_lines(_stream())
        det = st.case_details
        # plan 의 모든 case 가 상세에 존재 (드릴다운 가능).
        assert set(det) == {"c1", "c2", "c3", "c4"}
        assert det["c1"]["status"] == "pass" and det["c1"]["fail_count"] == 0
        assert det["c4"]["status"] == "pending"

    def test_repeated_fault_deduped_with_count(self):
        # until_pass 재샘플링으로 같은 fault 가 반복 emit 돼도 1줄(×count)로 접힌다.
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "fail", "elapsed_s": 10.0, "check": "custom_commands",
                   "reason": "ch3 bitrate (got: 5596kbps_ex=8192kbps)", "case_name": "c1"}),
            _line({"event_type": "fail", "elapsed_s": 15.0, "check": "custom_commands",
                   "reason": "ch3 bitrate (got: 5596kbps_ex=8192kbps)", "case_name": "c1"}),
            _line({"event_type": "fail", "elapsed_s": 20.0, "check": "custom_commands",
                   "reason": "ch3 bitrate (got: 5596kbps_ex=8192kbps)", "case_name": "c1"}),
        ])
        det = st.case_details["c1"]
        assert len(det["fails"]) == 1
        assert det["fails"][0]["count"] == 3
        assert det["fails"][0]["elapsed_s"] == 10.0       # 첫 발생
        assert det["fails"][0]["last_elapsed_s"] == 20.0  # 마지막 발생
        # 원시 이벤트 수는 그대로 보존.
        assert det["fail_count"] == 3


class TestChecklist:
    """case_start 의 설명 + 검증 항목(checklist) 캡처 및 항목별 상태 유도."""

    CHECKLIST = [
        {"name": "fps check", "command": "ffprobe ...", "expected": "OK"},
        {"name": "bps check", "command": "ffprobe ...", "expected": "OK"},
    ]

    def _events(self, end=None):
        evs = [
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1, "case_name": "c1",
                   "phase": "collect", "case_desc": "카메라 검증",
                   "checklist": self.CHECKLIST}),
        ]
        if end is not None:
            evs.append(_line(end))
        return ViewerState.from_lines(evs)

    def test_running_captures_desc_and_checklist_pending(self):
        st = self._events()
        det = st.case_details["c1"]
        assert det["desc"] == "카메라 검증"
        assert det["checks_total"] == 2
        assert det["checks_passed"] == 0
        assert [i["status"] for i in det["checklist"]] == ["running", "running"]
        assert det["checklist"][0]["command"] == "ffprobe ..."
        assert det["checklist"][0]["expected"] == "OK"

    def test_pass_marks_all_items_pass(self):
        st = self._events({"event_type": "case_end", "elapsed_s": 5.0,
                           "case_name": "c1", "phase": "validate", "result": "pass",
                           "completed_cases": 1, "pass_count": 1, "fail_count": 0,
                           "avg_case_duration_s": 5.0})
        det = st.case_details["c1"]
        assert det["checks_passed"] == 2
        assert all(i["status"] == "pass" for i in det["checklist"])

    def test_fail_marks_only_matched_item(self):
        st = self._events({"event_type": "case_end", "elapsed_s": 5.0,
                           "case_name": "c1", "phase": "validate", "result": "fail",
                           "completed_cases": 1, "pass_count": 0, "fail_count": 1,
                           "avg_case_duration_s": 5.0,
                           "reason": "fps check: mismatch (got: 30)"})
        det = st.case_details["c1"]
        by = {i["name"]: i["status"] for i in det["checklist"]}
        assert by == {"fps check": "fail", "bps check": "pass"}
        assert det["checks_passed"] == 1

    def test_checklist_results_attach_actual_and_authoritative_status(self):
        # case_end 의 checklist_results 가 있으면 항목별 실측값 부착 + per-item passed 가
        # reason 매칭보다 권위. 여기선 bps 가 fail 이지만 reason 엔 안 들어가도 fail 처리.
        st = self._events({"event_type": "case_end", "elapsed_s": 5.0,
                           "case_name": "c1", "phase": "validate", "result": "fail",
                           "completed_cases": 1, "pass_count": 0, "fail_count": 1,
                           "avg_case_duration_s": 5.0,
                           "reason": "some aggregate reason",
                           "checklist_results": [
                               {"name": "fps check", "actual": "30", "passed": True},
                               {"name": "bps check", "actual": "5596", "passed": False}]})
        det = st.case_details["c1"]
        by = {i["name"]: i for i in det["checklist"]}
        assert by["fps check"]["status"] == "pass"
        assert by["fps check"]["actual"] == "30"
        assert by["bps check"]["status"] == "fail"   # reason 무관, per-item passed 권위
        assert by["bps check"]["actual"] == "5596"
        assert det["checks_passed"] == 1

    def test_checklist_results_pass_shows_actuals(self):
        st = self._events({"event_type": "case_end", "elapsed_s": 5.0,
                           "case_name": "c1", "phase": "validate", "result": "pass",
                           "completed_cases": 1, "pass_count": 1, "fail_count": 0,
                           "avg_case_duration_s": 5.0,
                           "checklist_results": [
                               {"name": "fps check", "actual": "30", "passed": True},
                               {"name": "bps check", "actual": "8050", "passed": True}]})
        det = st.case_details["c1"]
        assert all(i["status"] == "pass" for i in det["checklist"])
        assert {i["name"]: i["actual"] for i in det["checklist"]} == {
            "fps check": "30", "bps check": "8050"}

    def test_pending_case_shows_checklist_from_run_start(self):
        # run_start 의 case_plans 로, 아직 시작 안 한 대기 케이스도 검증 항목을 미리 보여준다.
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1", "c2"], "total_cases": 2,
                   "case_plans": {"c2": {"desc": "케이스2 설명", "checklist": [
                       {"name": "x", "command": "cmd", "expected": "OK"}]}}}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
        ])
        det = st.case_details["c2"]
        assert det["status"] == "pending"
        assert det["desc"] == "케이스2 설명"
        assert det["checks_total"] == 1
        assert det["checklist"][0]["status"] == "pending"

    def test_no_checklist_when_absent(self):
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
        ])
        det = st.case_details["c1"]
        assert det["checklist"] == [] and det["checks_total"] == 0
        assert det["desc"] is None


class TestPendingEvents:
    """pending(준비 중) 이벤트는 fault 가 아님 — 분류/카운트에 영향 없음."""

    def test_pending_is_not_a_fault(self):
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "pending", "elapsed_s": 0.5, "check": "recording",
                   "reason": "NEED_2_FINALIZES", "case_name": "c1"}),
        ])
        assert st.fail_classification == {}      # fault 아님
        assert st.fail_summaries == {}
        assert st.pending_summaries == {"c1": "NEED_2_FINALIZES"}
        assert st.case_status["c1"] == "running"

    def test_pending_then_pass_is_clean(self):
        # 준비 중만 있다가 통과 → fault/회복 어디에도 안 뜨는 '깨끗한' 케이스.
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "pending", "elapsed_s": 0.5, "check": "recording",
                   "reason": "NEED_2_FINALIZES", "case_name": "c1"}),
            _line({"event_type": "case_end", "elapsed_s": 9.0, "case_name": "c1",
                   "phase": "validate", "result": "pass", "completed_cases": 1,
                   "pass_count": 1, "fail_count": 0, "avg_case_duration_s": 9.0}),
        ])
        assert st.fail_classification == {}
        assert st.pending_summaries == {}        # 종료 → 해제
        assert st.case_status["c1"] == "pass"
        assert st.case_details["c1"]["pending"] is None

    def test_pending_does_not_mask_real_fault(self):
        # 같은 케이스에 pending 후 실제 fail 이 오면 fault 로 분류된다.
        st = ViewerState.from_lines([
            _line({"event_type": "run_start", "elapsed_s": 0.0,
                   "cases": ["c1"], "total_cases": 1}),
            _line({"event_type": "case_start", "elapsed_s": 0.1,
                   "case_name": "c1", "phase": "collect"}),
            _line({"event_type": "pending", "elapsed_s": 0.5, "check": "recording",
                   "reason": "NEED_2_FINALIZES", "case_name": "c1"}),
            _line({"event_type": "fail", "elapsed_s": 1.0, "check": "process",
                   "reason": "gstApp 죽음", "case_name": "c1"}),
        ])
        assert st.fail_classification == {"c1": "active"}
        assert st.fail_summaries == {"c1": "gstApp 죽음"}
        # 실제 fault 가 오면 pending 은 해제된다 (⚠ FAULT 와 ⏳ 준비 중 동시 표시 방지).
        assert st.pending_summaries == {}
        assert st.case_details["c1"]["pending"] is None
