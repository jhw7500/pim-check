"""tests/test_event_lifecycle.py - PimEventStream lifecycle serializers.

event_stream.py 의 run_start / case_start / case_end / run_end / heartbeat
직렬화기 검증. 각 직렬화기는 개행 없는 단일 라인 JSON 을 돌려주고, 라인 단위로
파싱하여 상태를 재구성할 수 있어야 한다 (monotonic replay 가정).
"""
from __future__ import annotations

import json

import event_stream as es


def _parse(line: str) -> dict:
    # 직렬화 결과는 항상 개행이 없는 단일 라인이어야 한다.
    assert "\n" not in line
    return json.loads(line)


class TestRunStart:
    def test_fields_and_total_default(self):
        line = es.serialize_run_start(
            run_id="r1", plan="comprehensive", board="board-A",
            elapsed_s=0.0, cases=["a", "b", "c"], ts="2026-05-20T00:00:00Z",
        )
        rec = _parse(line)
        assert rec["event_type"] == "run_start"
        assert rec["run_id"] == "r1"
        assert rec["plan"] == "comprehensive"
        assert rec["board"] == "board-A"
        assert rec["elapsed_s"] == 0.0
        assert rec["ts"] == "2026-05-20T00:00:00Z"
        assert rec["cases"] == ["a", "b", "c"]
        # total_cases 미지정 시 cases 길이로 채운다.
        assert rec["total_cases"] == 3

    def test_total_explicit(self):
        rec = _parse(es.serialize_run_start(
            run_id="r1", plan="p", board="b", elapsed_s=1.0,
            cases=["a"], total_cases=5,
        ))
        assert rec["total_cases"] == 5


class TestCaseStart:
    def test_fields(self):
        rec = _parse(es.serialize_case_start(
            run_id="r1", plan="p", board="b", elapsed_s=2.5,
            case_name="fault_cam_disconnect", phase="collect",
        ))
        assert rec["event_type"] == "case_start"
        assert rec["case_name"] == "fault_cam_disconnect"
        assert rec["phase"] == "collect"
        assert rec["elapsed_s"] == 2.5


class TestCaseEnd:
    def test_pass_has_no_reason(self):
        rec = _parse(es.serialize_case_end(
            run_id="r1", plan="p", board="b", elapsed_s=3.0,
            case_name="c1", phase="validate", result="pass",
            completed_cases=1, pass_count=1, fail_count=0,
            avg_case_duration_s=3.0,
        ))
        assert rec["event_type"] == "case_end"
        assert rec["result"] == "pass"
        assert rec["completed_cases"] == 1
        assert rec["pass_count"] == 1
        assert rec["fail_count"] == 0
        assert rec["avg_case_duration_s"] == 3.0
        # pass 결과에는 reason 필드가 없어야 한다.
        assert "reason" not in rec

    def test_fail_has_reason_korean_preserved(self):
        rec = _parse(es.serialize_case_end(
            run_id="r1", plan="p", board="b", elapsed_s=9.0,
            case_name="c2", phase="validate", result="fail",
            completed_cases=2, pass_count=1, fail_count=1,
            avg_case_duration_s=4.5, reason="카메라 연결 끊김",
        ))
        assert rec["result"] == "fail"
        assert rec["fail_count"] == 1
        assert rec["reason"] == "카메라 연결 끊김"


class TestRunEnd:
    def test_fields(self):
        rec = _parse(es.serialize_run_end(
            run_id="r1", plan="p", board="b", elapsed_s=40.0,
            completed_cases=20, pass_count=18, fail_count=2,
        ))
        assert rec["event_type"] == "run_end"
        assert rec["completed_cases"] == 20
        assert rec["pass_count"] == 18
        assert rec["fail_count"] == 2


class TestHeartbeat:
    def test_fields(self):
        rec = _parse(es.serialize_heartbeat(
            run_id="r1", plan="p", board="b", elapsed_s=5.0, heartbeat_seq=1,
        ))
        assert rec["event_type"] == "heartbeat"
        assert rec["heartbeat_seq"] == 1
        assert rec["elapsed_s"] == 5.0


class TestCommonContract:
    def test_all_carry_always_present_fields(self):
        builders = [
            es.serialize_run_start(run_id="r", plan="p", board="b", elapsed_s=0.0, cases=["x"]),
            es.serialize_case_start(run_id="r", plan="p", board="b", elapsed_s=0.0, case_name="x", phase="collect"),
            es.serialize_case_end(run_id="r", plan="p", board="b", elapsed_s=0.0, case_name="x", phase="validate", result="pass", completed_cases=1, pass_count=1, fail_count=0, avg_case_duration_s=1.0),
            es.serialize_run_end(run_id="r", plan="p", board="b", elapsed_s=0.0, completed_cases=1, pass_count=1, fail_count=0),
            es.serialize_heartbeat(run_id="r", plan="p", board="b", elapsed_s=0.0, heartbeat_seq=1),
        ]
        for line in builders:
            rec = _parse(line)
            for field in ("event_type", "ts", "run_id", "plan", "board", "elapsed_s"):
                assert field in rec, f"{rec['event_type']} missing {field}"

    def test_ts_autofilled_when_omitted(self):
        rec = _parse(es.serialize_heartbeat(
            run_id="r", plan="p", board="b", elapsed_s=0.0, heartbeat_seq=1,
        ))
        # ts 미지정 시 ISO 8601 문자열이 자동으로 채워진다.
        assert isinstance(rec["ts"], str) and "T" in rec["ts"]
