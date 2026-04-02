from __future__ import annotations

import time

from checks import ALL_CHECKS
from ssh import SshTimeoutError, SshConnectionError


class Engine:
    """QA 체크 엔진 — 스냅샷 수집 및 모니터 루프."""

    def __init__(self, ssh, profile: dict) -> None:
        self.ssh = ssh
        self.profile = profile
        self.checks = list(ALL_CHECKS)

    def run_snapshot(self) -> list:
        """모든 체크를 한 번 실행하고 결과 목록을 반환한다.

        Returns:
            list of dict: {"name", "passed", "reason", "data"}
        """
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

            results.append(
                {
                    "name": check.name,
                    "passed": passed,
                    "reason": reason,
                    "data": data,
                }
            )

        return results

    def run_monitor(self) -> tuple[list, int, int]:
        """지정된 duration 동안 interval마다 스냅샷을 수집한다.

        Returns:
            (merged_results, samples_collected, samples_total)
        """
        duration = self.profile["monitor"]["duration_sec"]
        interval = self.profile["monitor"]["interval_sec"]
        samples_total = max(1, duration // interval)

        snapshots: list[list] = []
        start = time.time()

        while time.time() - start < duration:
            snapshots.append(self.run_snapshot())
            samples_collected = len(snapshots)
            if samples_collected >= samples_total:
                break
            time.sleep(interval)

        if not snapshots:
            snapshots.append(self.run_snapshot())

        samples_collected = len(snapshots)
        return (self.merge_snapshots(snapshots), samples_collected, samples_total)

    def merge_snapshots(self, snapshots: list[list]) -> list:
        """여러 스냅샷을 병합한다. 하나라도 실패하면 최종 결과도 실패다.

        Returns:
            list of dict: {"name", "passed", "reason", "data"}
        """
        merged: dict[str, dict] = {}

        for snapshot in snapshots:
            for entry in snapshot:
                name = entry["name"]
                if name not in merged:
                    merged[name] = dict(entry)
                else:
                    # WORST: 이미 실패면 유지, 현재가 실패면 교체
                    if not entry["passed"]:
                        merged[name] = dict(entry)

        return list(merged.values())
