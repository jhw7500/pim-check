"""
checks/recording.py - 녹화 연속성(세션 완료) 체크

현 FW 의 gstApp 은 세션 롤오버마다
``[GST][muxSinkBin.cpp:119] Session complete: <YYYYMMDD_HHMM>`` 를 로깅한다.
이 "Session complete" 발생 수로 녹화가 실제로 세션을 만들며 굴러가는지(연속성)를
검증한다.

설계 메모:
- 구 FW 는 "recording progress N/M" 포맷을 썼으나 현 FW 에서 폐기됨. 이를 grep 하던
  이전 구현은 녹화가 정상이어도 항상 0 매칭으로 FAIL 했다(2026-06 회귀 검증에서 확인).
- ``session_progress`` 설정값(예 "4/4")은 호환을 위해 보존하되, 의미는
  "non-null = 녹화 연속성 검증 요구"로 단순화한다. 채널별 파일/비트레이트 검증은
  custom_commands(BITRATE/파일무결성) + infer_agent(expected_channels) 가 담당한다.
"""
from __future__ import annotations

import re

from checks.base_check import BaseCheck


class RecordingCheck(BaseCheck):
    name = "recording"

    def collect(self, ssh, config: dict) -> dict:
        # 최근 200 라인에서 "Session complete" 발생 수를 센다.
        # since 시계 의존을 피해(부팅 직후 NTP 미동기) 라인 수 기반으로 robust 하게.
        # 필터는 Python 에서 — shell grep 은 no-match 시 exit 1 이라 ssh.run 이
        # 명령 실패로 처리할 수 있다(녹화 0세션 = FAIL 로 잡아야 할 케이스). journalctl
        # 만 실행해 SSH 명령이 항상 exit 0 이도록 한다.
        output = ssh.run("journalctl -t gstApp --no-pager -n 200 2>/dev/null")
        lines = [ln for ln in (output or "").splitlines() if "Session complete" in ln]
        latest = None
        if lines:
            m = re.search(r"Session complete:\s*(\S+)", lines[-1])
            if m:
                latest = m.group(1)
        return {
            "raw_output": output or "",
            "session_count": len(lines),
            "latest_session": latest,
        }

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        # session_progress 는 non-null 이면 "연속성 검증 활성"의 의미만 가지며, 값 자체는
        # 비교에 쓰이지 않는다(예: "4/4" → "1/4" 로 바꿔도 동작 동일).
        expected = config.get("recording", {}).get("session_progress")

        # 미설정(null)이면 연속성 검증을 요구하지 않음 → skip.
        if expected is None:
            return (True, "Skipped (no expected value configured)")

        count = data.get("session_count", 0)
        if count < 1:
            return (
                False,
                "No recording session completed in logs (recording stalled?)",
            )

        # latest 파싱 실패(포맷 drift 등) 시 'unknown' — 출력에 'None' 노출 방지.
        latest = data.get("latest_session") or "unknown"
        return (True, f"OK ({count} sessions, latest {latest})")
