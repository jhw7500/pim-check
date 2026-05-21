"""검증 단계 자동 retry 정책 (중앙 관리).

기본 검증 시간(stabilize_sec, monitor.duration_sec)은 그대로 두고,
첫 검증 결과가 안정화 미달(SSH 끊김, recovering, NEED_2_FINALIZES,
i2c 빈 응답 등)이면 일정 간격으로 snapshot을 다시 수집하여 재평가한다.

환경변수로 동작 조정 가능:
  PIM_VERIFY_MAX_ATTEMPTS  (default: 3)   — 첫 검증 포함 총 시도 횟수
  PIM_VERIFY_RETRY_WAIT    (default: 60)  — attempt 사이 대기 초

run_case (pim_check.py)와 _run_single_case (plan.py) 모두에서
동일 정책을 쓰도록 import 해서 사용한다.
"""
from __future__ import annotations

import os
import time
from typing import Callable

MAX_ATTEMPTS = int(os.environ.get("PIM_VERIFY_MAX_ATTEMPTS", "3"))
RETRY_WAIT_SEC = int(os.environ.get("PIM_VERIFY_RETRY_WAIT", "60"))

STABILIZATION_INDICATORS: tuple[str, ...] = (
    "NEED_2_FINALIZES",
    "recovering",
    "NO_SSH",
    "SETUP_EXCEPTION",
    "SshConnection",
    "SshTimeout",
    # 부팅/케이스 전환 직후 코어 프로세스가 아직 안 떴을 수 있다 — '준비 중'으로
    # 보고 재시도한다. 영속적으로 죽어 있으면 재시도 소진 후 최종 fail 로 남는다.
    # (process 체크 실패 형식: "<proc> is not running")
    "is not running",
)


def is_stabilization_reason(reason) -> bool:
    """단일 reason 문자열이 '장애가 아니라 아직 준비 안 됨' 신호인지 판정.

    NEED_2_FINALIZES / recovering / NO_SSH 등(STABILIZATION_INDICATORS) 또는 i2c
    register 빈 응답이면 True. fail 과 pending 을 가르는 단일 출처.
    """
    r = str(reason or "")
    if any(s in r for s in STABILIZATION_INDICATORS):
        return True
    # i2c register 빈 응답 (recovering 상태 의심)
    if "failed (got: )" in r or "failed (got: '')" in r:
        return True
    return False


def is_stabilization_fail(results: list) -> bool:
    """안정화 미달(=재시도 가치 있음) 시그널이 결과에 있는지 판정."""
    for r in results:
        if not isinstance(r, dict):
            continue
        if r.get("passed"):
            continue
        if "known_issue" in r:
            continue
        if is_stabilization_reason(r.get("reason", "")):
            return True
    return False


def _has_real_fail(results: list) -> bool:
    return any(
        isinstance(r, dict) and not r.get("passed") and "known_issue" not in r
        for r in results
    )


def run_verify_with_retry(
    engine,
    ssh,
    effective_duration: int = 0,
    log: Callable[[str], None] | None = None,
    until_pass: bool = False,
) -> tuple[list, int, int]:
    """첫 검증(snapshot 또는 monitor) → 안정화 의심이면 60s 대기 후 snapshot 재수집.

    Args:
        engine: Engine 인스턴스 (run_snapshot, run_monitor 보유)
        ssh: SshClient (check_connectivity 보유)
        effective_duration: monitor.duration_sec > 0 이면 monitor, 아니면 snapshot
        log: 진행 메시지 출력 콜백 (None이면 silent)
        until_pass: True 면 monitor 가 전 체크 통과 스냅샷에서 조기 종료(duration 은 상한).
                    finalize-aware sanity gate(smoke) 용 — 카메라 case 단축.

    Returns:
        (results, collected, total) — 마지막 채택된 검증 결과.

    재시도 정책:
      - attempt 1: 정상 snapshot 또는 monitor
      - attempt 2~MAX: 60s 대기 후 snapshot (monitor는 1회 더 돌리지 않음 — 시간 단축)
      - 안정화 indicator 없으면 첫 결과를 그대로 채택 (진짜 fail은 즉시 종료)
    """
    def _say(msg: str) -> None:
        if log:
            log(msg)

    # attempt 1
    if effective_duration <= 0:
        results = engine.run_snapshot()
        collected, total = 1, 1
    else:
        results, collected, total = engine.run_monitor(until_pass=until_pass)

    for attempt in range(2, MAX_ATTEMPTS + 1):
        if not _has_real_fail(results):
            break
        if not is_stabilization_fail(results):
            break  # 진짜 fail은 retry 가치 없음
        _say(f"  [verify retry {attempt}/{MAX_ATTEMPTS}] 안정화 의심 fail 감지, "
             f"{RETRY_WAIT_SEC}s 대기 후 snapshot 재수집...")
        time.sleep(RETRY_WAIT_SEC)
        if not ssh.check_connectivity():
            _say(f"  [verify retry {attempt}/{MAX_ATTEMPTS}] SSH 응답 없음, 다음 attempt 대기")
            continue
        try:
            new_results = engine.run_snapshot()
            results = new_results
            # snapshot 재실행은 단일 sample
            collected, total = 1, 1
        except Exception as e:
            _say(f"  [verify retry {attempt}/{MAX_ATTEMPTS}] snapshot 실패: {e}")
            continue

    return results, collected, total
