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
