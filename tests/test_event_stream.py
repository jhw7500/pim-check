"""event_stream.py 테스트 — JSONL Fail 이벤트 직렬화 및 write+flush."""
from __future__ import annotations

import functools
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock

from checks.base_check import BaseCheck
from event_stream import serialize_fail_event, serialize_pending_event, write_event


class TestSerializePendingEvent(unittest.TestCase):
    def test_pending_event_shape(self):
        line = serialize_pending_event(
            "recording", "FAIL:NEED_2_FINALIZES_AFTER_BOOT",
            run_id="r", case_name="c1", board=None,
        )
        self.assertNotIn("\n", line)
        rec = json.loads(line)
        self.assertEqual(rec["event_type"], "pending")
        self.assertEqual(rec["check"], "recording")
        self.assertEqual(rec["reason"], "FAIL:NEED_2_FINALIZES_AFTER_BOOT")
        self.assertEqual(rec["case_name"], "c1")
        self.assertIn("ts", rec)
        # None 값 필드는 제외된다 (fail 이벤트와 동일 규약).
        self.assertNotIn("board", rec)


class TestSerializeCaseStartPlan(unittest.TestCase):
    def test_case_start_carries_desc_and_checklist(self):
        from event_stream import serialize_case_start
        line = serialize_case_start(
            run_id="r", plan="smoke", board="b", elapsed_s=0.1,
            case_name="720p_2ch", phase="collect",
            case_desc="카메라 검증",
            checklist=[{"name": "fps", "command": "ffprobe", "expected": "OK"}],
        )
        rec = json.loads(line)
        self.assertEqual(rec["event_type"], "case_start")
        self.assertEqual(rec["case_desc"], "카메라 검증")
        self.assertEqual(rec["checklist"][0]["name"], "fps")

    def test_case_start_omits_plan_when_absent(self):
        from event_stream import serialize_case_start
        rec = json.loads(serialize_case_start(
            run_id="r", plan="p", board="b", elapsed_s=0.0,
            case_name="c1", phase="collect"))
        self.assertNotIn("case_desc", rec)
        self.assertNotIn("checklist", rec)


class TestRunStartPlans(unittest.TestCase):
    def test_run_start_carries_case_plans(self):
        from event_stream import serialize_run_start
        rec = json.loads(serialize_run_start(
            run_id="r", plan="p", board="b", elapsed_s=0, cases=["c1"],
            case_plans={"c1": {"desc": "d", "checklist": [{"name": "x"}]}}))
        self.assertEqual(rec["case_plans"]["c1"]["desc"], "d")

    def test_run_start_omits_plans_when_absent(self):
        from event_stream import serialize_run_start
        rec = json.loads(serialize_run_start(
            run_id="r", plan="p", board="b", elapsed_s=0, cases=["c1"]))
        self.assertNotIn("case_plans", rec)


class TestStabilizationReason(unittest.TestCase):
    def test_classifies_not_ready_vs_real_fault(self):
        from verify_retry import is_stabilization_reason
        self.assertTrue(is_stabilization_reason("FAIL:NEED_2_FINALIZES_AFTER_BOOT"))
        self.assertTrue(is_stabilization_reason("recovering..."))
        self.assertTrue(is_stabilization_reason("i2c failed (got: )"))
        # 부팅/케이스 전환 직후 코어 프로세스 미기동도 '준비 중'(retry 대상).
        self.assertTrue(is_stabilization_reason("BG_Check_for_pim is not running"))
        # 앞 공백 매칭 — 'is_not_running' 같은 프로세스명 자체는 오매칭하지 않는다.
        self.assertFalse(is_stabilization_reason("is_not_running_service CPU 99% out of range"))
        self.assertFalse(is_stabilization_reason("FAIL:30.1_ex=15"))
        self.assertFalse(is_stabilization_reason("gstApp 죽음"))
        self.assertFalse(is_stabilization_reason(""))


class TestSerializeFailEvent(unittest.TestCase):
    def test_emits_single_line_valid_json(self):
        line = serialize_fail_event("process", "gstApp is not running")
        # 단일 라인이어야 한다 (JSONL).
        self.assertNotIn("\n", line)
        # 라인이 JSON 으로 파싱되어야 한다.
        record = json.loads(line)
        self.assertEqual(record["event_type"], "fail")
        self.assertEqual(record["check"], "process")
        self.assertEqual(record["reason"], "gstApp is not running")
        self.assertIn("ts", record)

    def test_timestamp_is_iso8601(self):
        line = serialize_fail_event("thermal", "temp 95C > max 93C")
        record = json.loads(line)
        # ISO 8601 로 파싱 가능해야 한다.
        parsed = datetime.fromisoformat(record["ts"])
        self.assertIsNotNone(parsed)

    def test_explicit_ts_preserved(self):
        line = serialize_fail_event(
            "cam_state", "state='failed'", ts="2026-05-20T00:00:00+00:00"
        )
        record = json.loads(line)
        self.assertEqual(record["ts"], "2026-05-20T00:00:00+00:00")

    def test_unicode_reason_preserved(self):
        line = serialize_fail_event("log", "커널 패닉 감지")
        self.assertIn("커널 패닉 감지", line)
        record = json.loads(line)
        self.assertEqual(record["reason"], "커널 패닉 감지")

    def test_extra_fields_included_and_none_dropped(self):
        line = serialize_fail_event(
            "process",
            "down",
            case_name="fault_gstapp_crash",
            run_id="run-123",
            plan=None,
        )
        record = json.loads(line)
        self.assertEqual(record["case_name"], "fault_gstapp_crash")
        self.assertEqual(record["run_id"], "run-123")
        # 값이 None 인 필드는 제외되어야 한다.
        self.assertNotIn("plan", record)


class TestWriteEvent(unittest.TestCase):
    def test_line_readable_from_separate_handle_without_closing(self):
        # 핵심 보장: write_event 후 쓰기 핸들을 닫지 않아도 별도 핸들에서
        # 즉시 그 라인을 읽을 수 있어야 한다 (flush+fsync).
        line = serialize_fail_event("process", "gstApp is not running")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "events.jsonl")
            with open(path, "a", encoding="utf-8") as writer:
                write_event(writer, line)
                # writer 를 닫지 않은 상태로 별도 핸들에서 읽는다.
                with open(path, "r", encoding="utf-8") as reader:
                    contents = reader.read()
                self.assertIn(line, contents)
                # 정확히 한 줄(개행 종료)이어야 한다.
                self.assertEqual(contents, line + "\n")
                record = json.loads(contents.strip())
                self.assertEqual(record["event_type"], "fail")
                self.assertEqual(record["reason"], "gstApp is not running")

    def test_appends_multiple_lines_each_terminated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "events.jsonl")
            with open(path, "a", encoding="utf-8") as writer:
                write_event(writer, serialize_fail_event("a", "r1"))
                write_event(writer, serialize_fail_event("b", "r2"))
            with open(path, "r", encoding="utf-8") as reader:
                lines = reader.read().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["check"], "a")
            self.assertEqual(json.loads(lines[1])["check"], "b")

    def test_fail_line_on_disk_immediately_after_emit_returns(self):
        # Sub-AC 2 핵심 계약: emit(write_event) 호출이 반환된 직후, 쓰기 핸들을
        # 닫지 않은 상태에서도 Fail 이벤트 라인이 디스크 파일에 존재해야 한다
        # (버퍼링 지연 없음 = flush+fsync).
        line = serialize_fail_event("process", "gstApp is not running")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "events.jsonl")
            with open(path, "a", encoding="utf-8") as writer:
                write_event(writer, line)
                # write_event 가 반환된 직후, 완전히 새로 연 핸들로 디스크에서
                # 다시 읽는다. flush+fsync 가 없었다면 빈 내용이 보일 것이다.
                with open(path, "r", encoding="utf-8") as fresh:
                    on_disk = fresh.read()
            self.assertTrue(on_disk.endswith("\n"))
            record = json.loads(on_disk.strip())
            self.assertEqual(record["event_type"], "fail")
            self.assertEqual(record["check"], "process")
            self.assertEqual(record["reason"], "gstApp is not running")

    def test_does_not_double_newline(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "events.jsonl")
            with open(path, "a", encoding="utf-8") as writer:
                write_event(writer, "already-terminated\n")
                write_event(writer, "needs-newline")
            with open(path, "r", encoding="utf-8") as reader:
                lines = reader.readlines()
            self.assertEqual(lines, ["already-terminated\n", "needs-newline\n"])


class _StubCheck(BaseCheck):
    """validate() 결과를 주입할 수 있는 테스트용 BaseCheck 구현."""

    name = "stub"

    def __init__(self, result):
        self._result = result

    def collect(self, ssh, config):  # pragma: no cover - 이 AC 범위 밖
        return {}

    def validate(self, data, config):
        return self._result


class TestValidateAndEmit(unittest.TestCase):
    """validate() Fail 경로가 emitter 를 정확히 한 번 호출하는지 검증."""

    def test_fail_outcome_invokes_emitter_exactly_once(self):
        # emitter 를 스파이/스텁한다.
        emitter = MagicMock()
        check = _StubCheck((False, "gstApp is not running"))

        # validate() 를 Fail 결과로 구동한다.
        passed, reason = check.validate_and_emit({}, {}, emitter=emitter)

        # (passed, reason) 계약은 변하지 않는다.
        self.assertFalse(passed)
        self.assertEqual(reason, "gstApp is not running")
        # emitter 가 정확히 한 번 호출되어야 한다.
        emitter.assert_called_once()

    def test_emitter_receives_fail_event_payload(self):
        emitter = MagicMock()
        # 실제 fault(안정화 미달 아님) → fail 이벤트.
        check = _StubCheck((False, "gstApp CPU 412% out of range [0, 400]"))

        check.validate_and_emit(
            {}, {}, emitter=emitter, run_id="run-123", case_name="fault_gstapp_crash"
        )

        # emitter 인자가 Fail 이벤트(JSONL 한 줄)여야 한다.
        (line,) = emitter.call_args.args
        self.assertNotIn("\n", line)
        record = json.loads(line)
        self.assertEqual(record["event_type"], "fail")
        self.assertEqual(record["check"], "stub")
        self.assertEqual(record["reason"], "gstApp CPU 412% out of range [0, 400]")
        # context 필드가 Fail 이벤트에 전달되어야 한다.
        self.assertEqual(record["run_id"], "run-123")
        self.assertEqual(record["case_name"], "fault_gstapp_crash")

    def test_pass_outcome_does_not_invoke_emitter(self):
        # Pass 경로에서는 emitter 가 호출되지 않아야 ("exactly once on fail").
        emitter = MagicMock()
        check = _StubCheck((True, "OK"))

        passed, reason = check.validate_and_emit({}, {}, emitter=emitter)

        self.assertTrue(passed)
        emitter.assert_not_called()

    def test_no_emitter_is_backward_compatible(self):
        # emitter 미지정 시 예외 없이 (passed, reason) 만 반환한다.
        check = _StubCheck((False, "down"))
        self.assertEqual(check.validate_and_emit({}, {}), (False, "down"))


class TestValidateFailProducesOneJsonlLine(unittest.TestCase):
    """Sub-AC 3 핵심 계약: validate() 가 Fail 을 반환하면 JSONL 출력에
    정확히 한 줄의 Fail 이벤트가 기록되어야 한다.

    TestValidateAndEmit 는 MagicMock 스파이로 "emitter 가 한 번 호출됨"만
    검증한다. 여기서는 wiring(validate_and_emit) + 내구성 라이터(write_event)를
    실제 JSONL 파일에 결합하여, Fail 한 번이 디스크의 JSONL 출력에 '정확히 한 줄'
    의 fail 이벤트를 만들어내는지 종단 간으로 검증한다.
    """

    @staticmethod
    def _file_emitter(handle):
        """write_event 로 JSONL 파일에 한 줄을 append 하는 emitter 콜러블."""
        return functools.partial(write_event, handle)

    def test_fail_validate_writes_exactly_one_fail_line(self):
        check = _StubCheck((False, "gstApp CPU 412% out of range [0, 400]"))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "events.jsonl")
            with open(path, "a", encoding="utf-8") as handle:
                passed, reason = check.validate_and_emit(
                    {}, {},
                    emitter=self._file_emitter(handle),
                    run_id="run-123",
                    plan="comprehensive",
                    board="board-A",
                    case_name="fault_gstapp_crash",
                )
            # (passed, reason) 계약은 변하지 않는다.
            self.assertFalse(passed)
            self.assertEqual(reason, "gstApp CPU 412% out of range [0, 400]")
            # JSONL 출력에 정확히 한 줄의 fail 이벤트가 있어야 한다.
            with open(path, "r", encoding="utf-8") as reader:
                lines = reader.read().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["event_type"], "fail")
            self.assertEqual(record["check"], "stub")
            self.assertEqual(record["reason"], "gstApp CPU 412% out of range [0, 400]")
            self.assertEqual(record["case_name"], "fault_gstapp_crash")

    def test_pass_validate_writes_no_line(self):
        # "exactly one on fail" — Pass 결과는 JSONL 출력에 어떤 라인도 남기지 않는다.
        check = _StubCheck((True, "OK"))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "events.jsonl")
            with open(path, "a", encoding="utf-8") as handle:
                check.validate_and_emit({}, {}, emitter=self._file_emitter(handle))
            with open(path, "r", encoding="utf-8") as reader:
                self.assertEqual(reader.read(), "")


if __name__ == "__main__":
    unittest.main()
