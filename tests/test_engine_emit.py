"""tests/test_engine_emit.py - Engine.run_snapshot 실시간 fail 이벤트 emit.

validate() 가 Fail 을 돌려주는 순간 base_check.validate_and_emit 를 통해 단일
fail 이벤트가 emit 되는지 검증한다. emitter 가 없으면 기존 동작과 동일(back-compat).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from checks.base_check import BaseCheck
from engine import Engine


class _PassCheck(BaseCheck):
    name = "cpu"

    def collect(self, ssh, config):
        return {}

    def validate(self, data, config):
        return (True, "OK")


class _FailCheck(BaseCheck):
    name = "process"

    def collect(self, ssh, config):
        return {}

    def validate(self, data, config):
        return (False, "gstApp 죽음")


class _PendingCheck(BaseCheck):
    name = "recording"

    def collect(self, ssh, config):
        return {}

    def validate(self, data, config):
        return (False, "ch0 fps ... (got: FAIL:NEED_2_FINALIZES_AFTER_BOOT)")


def _engine(checks, **kw):
    eng = Engine(MagicMock(), {"checks": {}}, **kw)
    eng.checks = checks
    return eng


def test_stabilization_reason_emits_pending_not_fail():
    # NEED_2_FINALIZES 는 장애가 아니라 '준비 중' → pending 이벤트로 emit.
    emitted: list[str] = []
    eng = _engine([_PendingCheck()], emitter=emitted.append,
                  emit_context={"case_name": "c1"})
    eng.run_snapshot()
    assert len(emitted) == 1
    rec = json.loads(emitted[0])
    assert rec["event_type"] == "pending"
    assert rec["check"] == "recording"
    assert rec["case_name"] == "c1"


def test_real_fault_still_emits_fail():
    emitted: list[str] = []
    eng = _engine([_FailCheck()], emitter=emitted.append, emit_context={"case_name": "c1"})
    eng.run_snapshot()
    assert json.loads(emitted[0])["event_type"] == "fail"


def test_emits_one_fail_event_with_context():
    emitted: list[str] = []
    eng = _engine(
        [_PassCheck(), _FailCheck()],
        emitter=emitted.append,
        emit_context={"run_id": "r", "plan": "comprehensive",
                      "board": "board-A", "case_name": "c1"},
    )
    results = eng.run_snapshot()

    # pass 체크는 check_pass, fail 체크는 fail — 총 2개.
    assert len(emitted) == 2
    recs = [json.loads(e) for e in emitted]
    rec = next(r for r in recs if r["event_type"] == "fail")
    assert rec["event_type"] == "fail"
    assert rec["check"] == "process"
    assert rec["reason"] == "gstApp 죽음"
    assert rec["case_name"] == "c1"
    assert rec["run_id"] == "r"
    # (passed, reason) 계약은 그대로 — 결과 목록도 정상.
    assert results[1]["passed"] is False
    assert results[1]["reason"] == "gstApp 죽음"


def test_no_emitter_is_backward_compatible():
    eng = _engine([_FailCheck()])  # emitter 없음
    results = eng.run_snapshot()
    # emit 없이도 결과는 동일하게 산출된다.
    assert results[0]["passed"] is False
    assert results[0]["reason"] == "gstApp 죽음"


def test_pass_emits_check_pass_event():
    emitted: list[str] = []
    eng = _engine([_PassCheck()], emitter=emitted.append,
                  emit_context={"case_name": "c1"})
    eng.run_snapshot()
    assert len(emitted) == 1
    rec = json.loads(emitted[0])
    assert rec["event_type"] == "check_pass"
    assert rec["check"] == "cpu"
    assert rec["case_name"] == "c1"
