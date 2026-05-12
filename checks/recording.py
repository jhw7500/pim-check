"""
checks/recording.py - 녹화 진행 상태 체크
"""
from __future__ import annotations

import re

from checks.base_check import BaseCheck


class RecordingCheck(BaseCheck):
    name = "recording"

    def collect(self, ssh, config: dict) -> dict:
        # since 시계 의존 — 부팅 직후 NTP 미동기 상태에서 빈 결과 가능
        # 최근 200 라인에서 progress 찾는 방식이 더 robust
        output = ssh.run(
            "journalctl -t gstApp --no-pager -n 200 2>/dev/null"
            " | grep -i 'progress' | tail -1"
        )

        progress = None
        if output:
            m = re.search(r"(\d+/\d+)", output)
            if m:
                progress = m.group(1)

        return {"raw_output": output or "", "progress": progress}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        expected_progress = config.get("recording", {}).get("session_progress")

        if expected_progress is None:
            return (True, "Skipped (no expected value configured)")

        actual = data.get("progress")
        if actual is None:
            return (False, "No recording progress found in logs")

        if actual != expected_progress:
            return (False, f"Progress {actual} != expected {expected_progress}")

        return (True, "OK")
