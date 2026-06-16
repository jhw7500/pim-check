"""
tests/test_checks_recording.py - RecordingCheck 단위 테스트

현 FW gstApp 은 세션 롤오버마다 "[GST][muxSinkBin.cpp:119] Session complete: <YYYYMMDD_HHMM>"
로깅한다. RecordingCheck 는 이 "Session complete" 발생 수로 녹화 연속성(세션이 실제로
완료되며 굴러가는지)을 검증한다. (구 FW 의 "recording progress N/M" 포맷은 폐기됨)
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.recording import RecordingCheck


# session_progress 가 non-null 이면 "녹화 연속성 검증 요구" 의미 (값 자체는 호환용 보존).
CONFIG = {"recording": {"expected_channels": 4, "session_progress": "4/4"}}
CONFIG_NO_PROGRESS = {"recording": {"expected_channels": 4}}

# journalctl 원본(필터 전) — 노이즈 라인이 섞여 있어도 "Session complete"만 세야 한다.
LOG_TWO_SESSIONS = (
    "Jun 16 05:01:00 t gstApp[581]: [GST][main.cpp:463] All channels aligned to 00s. Next target : 02m 00s\n"
    "Jun 16 05:01:00 t gstApp[581]: [GST][muxSinkBin.cpp:119] Session complete: 20260616_0500\n"
    "Jun 16 05:02:00 t gstApp[581]: [GST][main.cpp:463] All channels aligned to 00s. Next target : 03m 00s\n"
    "Jun 16 05:02:00 t gstApp[581]: [GST][muxSinkBin.cpp:119] Session complete: 20260616_0501"
)


class TestRecordingCheckCollect(unittest.TestCase):
    def setUp(self):
        self.check = RecordingCheck()

    def test_collect_counts_session_complete(self):
        """'Session complete' 2줄 → session_count=2, latest=마지막 세션 id"""
        ssh = MagicMock()
        ssh.run.return_value = LOG_TWO_SESSIONS

        data = self.check.collect(ssh, CONFIG)

        self.assertEqual(data["session_count"], 2)
        self.assertEqual(data["latest_session"], "20260616_0501")

    def test_collect_no_output(self):
        """ssh.run() → None 반환 시 session_count=0"""
        ssh = MagicMock()
        ssh.run.return_value = None

        data = self.check.collect(ssh, CONFIG)

        self.assertEqual(data["session_count"], 0)
        self.assertIsNone(data["latest_session"])

    def test_collect_session_without_parsable_id(self):
        """'Session complete' 는 있으나 id 파싱 불가(포맷 drift) → count는 세되 latest=None"""
        ssh = MagicMock()
        ssh.run.return_value = "Jun 16 t gstApp[1]: [GST] Session complete"  # ': <id>' 없음

        data = self.check.collect(ssh, CONFIG)

        self.assertEqual(data["session_count"], 1)
        self.assertIsNone(data["latest_session"])

    def test_validate_passes_with_unknown_latest(self):
        """latest 파싱 실패해도 count>=1 이면 PASS, 출력에 'None' 대신 'unknown'"""
        data = {"raw_output": "Session complete", "session_count": 1, "latest_session": None}
        passed, reason = self.check.validate(data, CONFIG)
        self.assertTrue(passed)
        self.assertIn("unknown", reason)
        self.assertNotIn("None", reason)


class TestRecordingCheckValidate(unittest.TestCase):
    def setUp(self):
        self.check = RecordingCheck()

    def test_validate_sessions_present_passes(self):
        """session_count>=1 + config 설정 → (True, OK)"""
        data = {"raw_output": LOG_TWO_SESSIONS, "session_count": 2, "latest_session": "20260616_0501"}
        passed, reason = self.check.validate(data, CONFIG)
        self.assertTrue(passed)
        self.assertIn("2", reason)

    def test_validate_no_session_fails(self):
        """session_count=0 + config 설정 → (False, 녹화 미진행)"""
        data = {"raw_output": "", "session_count": 0, "latest_session": None}
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("session", reason.lower())

    def test_validate_null_config_skips(self):
        """session_progress 미설정 → (True, 'Skipped...')"""
        data = {"raw_output": "", "session_count": 0, "latest_session": None}
        passed, reason = self.check.validate(data, CONFIG_NO_PROGRESS)
        self.assertTrue(passed)
        self.assertIn("Skipped", reason)


if __name__ == "__main__":
    unittest.main()
