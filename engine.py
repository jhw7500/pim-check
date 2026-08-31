from __future__ import annotations

import copy
import re
import time

# fail reason 에서 변동 측정값(숫자)만 마스킹해 sample 간 노이즈를 제거한다.
# lookbehind (?<![A-Za-z0-9]) 로 '영숫자에 붙은 숫자'(chN/i2cN 같은 식별자)는 보존하고,
# 구분자(공백/콜론/= 등) 뒤에서 시작하는 숫자(측정값: bitrate/fps/temp)만 '#'로 친다.
# 식별자 숫자까지 마스킹하면 ch1↔ch3 실패가 같은 시그니처로 접혀 false 조기종료(PR #29 claude).
# 0-9 도 lookbehind 에 넣어야 다중 자리(ch10→ch1#)에서 뒷자리만 매칭되는 버그를 막는다(PR #30 gemini).
_MEASUREMENT_RE = re.compile(r"(?<![A-Za-z0-9])\d+")

from checks import checks_for_scope
from ssh import SshClient, SshTimeoutError, SshConnectionError
from verify_retry import is_stabilization_reason

DEFAULT_SHUTDOWN_TIMEOUT = 600  # 10분
DEFAULT_SHUTDOWN_POLL = 60      # 1분
# until_pass 모니터에서 동일한 '실제 fail'(비-stabilization)이 이만큼 연속 관측되면
# 지속 결함으로 판단해 조기 종료한다(전 구간 대기 회피).
STABLE_FAIL_SAMPLES = 3


class Engine:
    """QA 체크 엔진 — 스냅샷 수집 및 모니터 루프."""

    def __init__(self, ssh: SshClient, profile: dict,
                 emitter=None, emit_context: dict | None = None) -> None:
        self.ssh = ssh
        self.profile = profile
        self.checks = checks_for_scope("snapshot")
        # 실시간 이벤트 emit (선택). emitter 가 있으면 validate Fail 순간 단일 fail
        # 이벤트를 emit_context(run_id/plan/board/case_name 등)와 함께 내보낸다.
        # None 이면 기존 validate 경로 그대로 (back-compat).
        self.emitter = emitter
        self.emit_context = emit_context or {}

    def run_snapshot(self, retries: int = 1) -> list:
        """모든 체크를 한 번 실행하고 결과 목록을 반환한다.

        Args:
            retries: SSH 에러 발생 시 기본 재시도 횟수 (retry_policy로 체크별 override 가능)
        """
        config = self.profile.get("checks", {})
        retry_policy = self.profile.get("retry_policy", {})
        results = []

        for check in self.checks:
            _check_start = time.time()
            data = {}
            passed = False
            reason = ""
            check_retries = retry_policy.get(check.name, retries)

            for attempt in range(1 + check_retries):
                try:
                    data = check.collect(self.ssh, config)
                    if self.emitter is not None and hasattr(check, "validate_and_emit"):
                        # validate Fail 순간 단일 fail 이벤트를 실시간 emit.
                        passed, reason = check.validate_and_emit(
                            data, config, emitter=self.emitter, **self.emit_context,
                        )
                    else:
                        passed, reason = check.validate(data, config)
                    break
                except (SshTimeoutError, SshConnectionError) as exc:
                    if attempt < check_retries:
                        time.sleep(2)
                        continue
                    data = {}
                    passed = False
                    reason = f"SSH_ERROR: {exc}"

            results.append({
                "name": check.name,
                "passed": passed,
                "reason": reason,
                "data": data,
                "duration_ms": int((time.time() - _check_start) * 1000),
            })

        return results

    def _detect_thermal_shutdown(self) -> bool:
        """SSH 연결이 끊어졌는지 확인. 끊어졌으면 thermal shutdown 가능성."""
        return not self.ssh.check_connectivity()

    def _wait_for_recovery(self, timeout: int = DEFAULT_SHUTDOWN_TIMEOUT,
                           poll_interval: int = DEFAULT_SHUTDOWN_POLL,
                           stabilize_sec: int = 30) -> bool:
        """타겟 복귀를 대기한다. 복귀하면 True, 타임아웃이면 False."""
        print("Target unreachable — possible thermal shutdown")
        print(f"Waiting up to {timeout}s for recovery (polling every {poll_interval}s)...")

        elapsed = 0
        while elapsed < timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval
            if self.ssh.check_connectivity():
                print(f"Target recovered after {elapsed}s — stabilizing for {stabilize_sec}s...")
                time.sleep(stabilize_sec)
                return True
            print(f"  still down... ({elapsed}/{timeout}s)")

        print(f"Target did not recover within {timeout}s")
        return False

    def _real_fail_signature(self, snap):
        """스냅샷에서 '실제 fail'(passed=False & 비-stabilization)의 시그니처 반환.

        NEED_2_FINALIZES / recovering / process 미기동 등 '준비 중' 신호는 제외한다
        (그건 시간이 지나면 풀릴 수 있으므로 조기 종료 대상이 아님). 실제 fail 이 없으면
        None, 있으면 (name, 정규화 reason) 쌍의 frozenset.

        시그니처는 (name, 숫자를 마스킹한 reason)을 쓴다:
          - reason 의 측정값(bitrate/온도 등)은 sample 마다 흔들리므로(5596 vs 5601kbps)
            숫자를 '#'로 치환해 같은 fail 로 본다 → 동적 측정값 fail 도 조기 종료가 동작.
          - 하지만 reason 의 '종류'(어떤 항목/메시지)는 보존한다 → custom_commands 처럼
            여러 sub-command 를 묶는 집계 체크에서, 수렴 중 서로 다른 sub-command 가
            샘플마다 다르게 실패하면 시그니처가 달라져 조기 종료하지 않고 계속 샘플링한다
            (name-only 로 접으면 이 경우를 지속 결함으로 오판해 false 종료 — PR #27 codex 지적).
          - 마스킹은 측정값에만 적용한다(_MEASUREMENT_RE): chN/i2cN 같은 식별자 숫자는
            보존해 ch1↔ch3 실패가 같은 시그니처로 접히는 false 종료를 막는다(PR #29 claude 지적).
        """
        if not snap:
            return None
        sig = frozenset(
            (r.get("name"), _MEASUREMENT_RE.sub("#", r.get("reason") or ""))
            for r in snap
            if isinstance(r, dict) and not r.get("passed")
            and not is_stabilization_reason(r.get("reason") or "")
        )
        return sig or None

    def run_monitor(self, until_pass: bool = False) -> tuple[list, int, int]:
        """지정된 duration 동안 interval마다 스냅샷을 수집한다.

        모니터링 중 SSH 연결 끊김(thermal shutdown 등)이 발생하면:
        1. 최대 10분간 복귀 대기 (1분 주기)
        2. 복귀하면 모니터링 계속
        3. 타임아웃이면 수집된 결과로 리포트

        until_pass=True (finalize-aware / sanity gate 모드):
            전 체크가 통과한 스냅샷이 처음 나오는 즉시 종료한다. duration 은 상한으로만
            쓰인다. 카메라 case 처럼 "부팅 후 녹화 finalize 2개"가 갖춰지면 통과하는
            검증에서, 고정 300s 를 끝까지 기다리지 않고 준비되는 즉시 끝낸다. 통과 전의
            일시 fail(NEED_2_FINALIZES 등)은 merge 하지 않고 통과 스냅샷만 반환한다.

        Returns:
            (merged_results, samples_collected, samples_total)
        """
        duration = self.profile["monitor"]["duration_sec"]
        interval = self.profile["monitor"]["interval_sec"]
        if interval <= 0:
            interval = 5
        samples_total = max(1, duration // interval)

        snapshots: list[list] = []
        start = time.time()
        consecutive_failures = 0
        stable_sig = None      # 직전 '실제 fail' 시그니처 (frozenset)
        stable_streak = 0      # 동일 실제 fail 연속 횟수

        while time.time() - start < duration:
            # 스냅샷 실행 전 연결 확인
            if self._detect_thermal_shutdown():
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    # 2회 연속 연결 실패 → thermal shutdown 판단
                    recovered = self._wait_for_recovery()
                    if not recovered:
                        break
                    consecutive_failures = 0
                    # 복귀 후 시간 재계산 — 남은 시간만큼 계속
                    continue
                else:
                    time.sleep(interval)
                    continue

            consecutive_failures = 0
            snap = self.run_snapshot()
            snapshots.append(snap)

            if until_pass:
                # early-exit-on-pass: 전 체크 통과 스냅샷이 나오면 즉시 종료.
                # 통과 스냅샷만 권위로 반환한다(merge 시 직전의 일시 fail 이 살아남는 것 방지).
                if snap and all(isinstance(r, dict) and r.get("passed") for r in snap):
                    return (snap, len(snapshots), samples_total)
                # stable-fail early-exit: 동일한 '실제 fail'(비-stabilization)이
                # STABLE_FAIL_SAMPLES 회 연속이면 지속 결함으로 보고 종료.
                # NEED_2_FINALIZES 등 '준비 중'만 있으면 streak 리셋(계속 대기).
                sig = self._real_fail_signature(snap)
                if sig is None:
                    stable_sig, stable_streak = None, 0
                elif sig == stable_sig:
                    stable_streak += 1
                else:
                    stable_sig, stable_streak = sig, 1
                if stable_streak >= STABLE_FAIL_SAMPLES:
                    return (snap, len(snapshots), samples_total)

            if len(snapshots) >= samples_total:
                break
            time.sleep(interval)

        if not snapshots:
            # 한 번도 수집 못 했으면 마지막으로 시도
            if self.ssh.check_connectivity():
                snapshots.append(self.run_snapshot())

        samples_collected = len(snapshots)
        merged = self.merge_snapshots(snapshots) if snapshots else []
        return (merged, samples_collected, samples_total)

    def merge_snapshots(self, snapshots: list[list]) -> list:
        """여러 스냅샷을 병합한다. 하나라도 실패하면 최종 결과도 실패."""
        merged: dict[str, dict] = {}

        for snapshot in snapshots:
            for entry in snapshot:
                name = entry["name"]
                if name not in merged:
                    merged[name] = copy.deepcopy(entry)
                else:
                    if not entry["passed"]:
                        merged[name] = copy.deepcopy(entry)

        return list(merged.values())
