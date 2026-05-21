"""event_stream.py — pim_check.py 실행을 위한 JSONL 이벤트 직렬화.

PimEventStream 스키마의 한 줄(JSONL) 레코드를 만든다. 각 라인은 그 자체로
완결적인 JSON 객체이며, 라인 단위로 파싱하여 상태를 재구성할 수 있다.

serialize_* 함수는 순수 직렬화 로직만 담는다 (SSH/파일 IO 없음). 직렬화된
라인을 events/<ts>_<plan>_<board>.jsonl 에 즉시 기록(flush+fsync)하는 내구성
헬퍼는 write_event() 가 제공한다. pim_check.py 및 checks/* 의 emit 훅에서
직렬화 후 write_event() 로 한 줄씩 append 한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _now_iso() -> str:
    """이벤트 발행 시각을 ISO 8601 문자열로 반환한다 (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def serialize_fail_event(check, reason, *, ts=None, **fields) -> str:
    """validate() Fail 결과를 단일 한 줄 JSONL fail 이벤트로 직렬화한다.

    Args:
        check: Fail 을 보고한 체크의 id/name (예: "process").
        reason: validate() 가 반환한 사람이 읽을 수 있는 오류 요약.
        ts: 선택적 ISO 8601 타임스탬프. None 이면 현재 UTC 시각을 사용한다.
        **fields: 추가 컨텍스트 필드(run_id, plan, board, case_name 등).
                  값이 None 인 필드는 제외된다.

    Returns:
        개행 문자가 없는 단일 라인 JSON 문자열. ``json.loads`` 로 파싱 가능하며
        항상 ``event_type`` == "fail", ``ts``, ``check``, ``reason`` 을 포함한다.
    """
    record = {
        "event_type": "fail",
        "ts": ts if ts is not None else _now_iso(),
        "check": check,
        "reason": reason,
    }
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    # json.dumps 기본 출력에는 개행이 없으므로 결과는 항상 한 줄이다.
    # ensure_ascii=False 로 한글 reason 을 그대로 보존한다.
    return json.dumps(record, ensure_ascii=False)


def serialize_pending_event(check, reason, *, ts=None, **fields) -> str:
    """안정화 미달(준비 중) 상태를 단일 한 줄 JSONL ``pending`` 이벤트로 직렬화한다.

    ``fail`` 과 동일한 구조지만 ``event_type`` == "pending". NEED_2_FINALIZES 처럼
    "장애가 아니라 아직 준비 안 됨"인 결과를 fault 와 구분해 표면화하기 위한 것.
    뷰어는 이를 fault 로 세지 않고 "준비 중"으로만 표시한다.
    """
    record = {
        "event_type": "pending",
        "ts": ts if ts is not None else _now_iso(),
        "check": check,
        "reason": reason,
    }
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    return json.dumps(record, ensure_ascii=False)


def _base_event(event_type: str, run_id: str, plan: str, board: str,
                elapsed_s: float, ts) -> dict:
    """모든 lifecycle 이벤트에 공통으로 존재하는 필드 묶음.

    PimEventStream 의 '항상-존재' 필드(event_type, ts, run_id, plan, board,
    elapsed_s)를 채운다. 각 직렬화기는 여기에 이벤트별 필드를 더한다.
    """
    return {
        "event_type": event_type,
        "ts": ts if ts is not None else _now_iso(),
        "run_id": run_id,
        "plan": plan,
        "board": board,
        "elapsed_s": elapsed_s,
    }


def serialize_run_start(*, run_id, plan, board, elapsed_s, cases,
                        total_cases=None, case_plans=None, ts=None) -> str:
    """run 시작 이벤트. 전체 case 목록과 총 개수를 담는다 (run_start 에만 존재).

    total_cases 미지정 시 ``len(cases)`` 로 채운다. case_plans 가 주어지면
    {case_name: {"desc":..., "checklist":[...]}} 형태로 함께 실어, 아직 시작 안 한
    대기 케이스도 뷰어가 검증 항목을 미리 보여줄 수 있게 한다.
    """
    rec = _base_event("run_start", run_id, plan, board, elapsed_s, ts)
    rec["cases"] = list(cases)
    rec["total_cases"] = total_cases if total_cases is not None else len(rec["cases"])
    if case_plans is not None:
        rec["case_plans"] = case_plans
    return json.dumps(rec, ensure_ascii=False)


def serialize_case_start(*, run_id, plan, board, elapsed_s, case_name,
                         phase, case_desc=None, checklist=None, ts=None) -> str:
    """case 시작 이벤트. 현재 실행 case 식별 + 단계(collect/validate).

    case_desc/checklist 가 주어지면 뷰어가 "이 케이스가 무엇을·어떻게 검증하는지"
    를 보여줄 수 있도록 함께 싣는다(드릴다운 체크리스트 + 검증 방법). 둘 다 선택.
    checklist 항목: {"name": ..., "command": ..., "expected": ...}.
    """
    rec = _base_event("case_start", run_id, plan, board, elapsed_s, ts)
    rec["case_name"] = case_name
    rec["phase"] = phase
    if case_desc is not None:
        rec["case_desc"] = case_desc
    if checklist is not None:
        rec["checklist"] = checklist
    return json.dumps(rec, ensure_ascii=False)


def serialize_case_end(*, run_id, plan, board, elapsed_s, case_name, phase,
                       result, completed_cases, pass_count, fail_count,
                       avg_case_duration_s, reason=None, checklist_results=None,
                       ts=None) -> str:
    """case 종료 이벤트. 결과 + 누적 카운트 + ETA 산정용 평균 소요시간.

    reason 은 ``result == "fail"`` 일 때(혹은 명시적으로 전달될 때)만 포함한다.
    checklist_results 가 주어지면 항목별 실측값/통과여부 [{name, actual, passed}] 를
    함께 실어, 뷰어가 '측정 vs 기대' 를 항목 단위로 보여줄 수 있게 한다.
    """
    rec = _base_event("case_end", run_id, plan, board, elapsed_s, ts)
    rec["case_name"] = case_name
    rec["phase"] = phase
    rec["result"] = result
    rec["completed_cases"] = completed_cases
    rec["pass_count"] = pass_count
    rec["fail_count"] = fail_count
    rec["avg_case_duration_s"] = avg_case_duration_s
    if reason is not None:
        rec["reason"] = reason
    if checklist_results is not None:
        rec["checklist_results"] = checklist_results
    return json.dumps(rec, ensure_ascii=False)


def serialize_run_end(*, run_id, plan, board, elapsed_s, completed_cases,
                      pass_count, fail_count, ts=None) -> str:
    """run 종료 이벤트. 최종 누적 카운트."""
    rec = _base_event("run_end", run_id, plan, board, elapsed_s, ts)
    rec["completed_cases"] = completed_cases
    rec["pass_count"] = pass_count
    rec["fail_count"] = fail_count
    return json.dumps(rec, ensure_ascii=False)


def serialize_heartbeat(*, run_id, plan, board, elapsed_s, heartbeat_seq,
                        ts=None) -> str:
    """heartbeat 이벤트. producer 생존 신호 — state 복원 시 무시 가능한 noop."""
    rec = _base_event("heartbeat", run_id, plan, board, elapsed_s, ts)
    rec["heartbeat_seq"] = heartbeat_seq
    return json.dumps(rec, ensure_ascii=False)


def write_event(handle, line: str) -> None:
    """직렬화된 이벤트 한 줄을 열린 핸들에 append 하고 즉시 flush+fsync 한다.

    핸들을 닫지 않아도 다른 핸들/프로세스(예: pim_viewer)가 해당 라인을 즉시
    읽을 수 있도록 보장한다. fault/fail 이벤트의 실시간 가시성(감지 후 1초 이내
    flush)을 위한 내구성 헬퍼다.

    Args:
        handle: 텍스트 모드(append)로 열린, ``fileno()`` 를 제공하는 파일 객체.
        line: ``serialize_*`` 가 만든, 개행이 없는 단일 라인 JSON 문자열.
              개행으로 끝나지 않으면 자동으로 한 개의 ``\\n`` 을 덧붙인다.
    """
    if not line.endswith("\n"):
        line += "\n"
    handle.write(line)
    # 파이썬 버퍼 → OS 버퍼로 비우고, OS 버퍼 → 디스크로 강제 동기화한다.
    # 두 단계를 모두 거쳐야 별도 핸들에서 즉시 라인을 읽을 수 있다.
    handle.flush()
    os.fsync(handle.fileno())
