from __future__ import annotations

import copy
import time

from checks import ALL_CHECKS
from ssh import SshClient, SshTimeoutError, SshConnectionError

DEFAULT_SHUTDOWN_TIMEOUT = 600  # 10분
DEFAULT_SHUTDOWN_POLL = 60      # 1분


class Engine:
    """QA 체크 엔진 — 스냅샷 수집 및 모니터 루프."""

    def __init__(self, ssh: SshClient, profile: dict) -> None:
        self.ssh = ssh
        self.profile = profile
        self.checks = list(ALL_CHECKS)

    def run_snapshot(self) -> list:
        """모든 체크를 한 번 실행하고 결과 목록을 반환한다."""
        config = self.profile.get("checks", {})
        results = []

        for check in self.checks:
            try:
                data = check.collect(self.ssh, config)
                passed, reason = check.validate(data, config)
            except (SshTimeoutError, SshConnectionError) as exc:
                data = {}
                passed = False
                reason = f"SSH_ERROR: {exc}"

            results.append({
                "name": check.name,
                "passed": passed,
                "reason": reason,
                "data": data,
            })

        return results

    def _detect_thermal_shutdown(self) -> bool:
        """SSH 연결이 끊어졌는지 확인. 끊어졌으면 thermal shutdown 가능성."""
        return not self.ssh.check_connectivity()

    def _wait_for_recovery(self, timeout: int = DEFAULT_SHUTDOWN_TIMEOUT,
                           poll_interval: int = DEFAULT_SHUTDOWN_POLL,
                           stabilize_sec: int = 30) -> bool:
        """타겟 복귀를 대기한다. 복귀하면 True, 타임아웃이면 False."""
        print(f"Target unreachable — possible thermal shutdown")
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

    def run_monitor(self) -> tuple[list, int, int]:
        """지정된 duration 동안 interval마다 스냅샷을 수집한다.

        모니터링 중 SSH 연결 끊김(thermal shutdown 등)이 발생하면:
        1. 최대 10분간 복귀 대기 (1분 주기)
        2. 복귀하면 모니터링 계속
        3. 타임아웃이면 수집된 결과로 리포트

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
            snapshots.append(self.run_snapshot())

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
