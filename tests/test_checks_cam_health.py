"""
tests/test_checks_cam_health.py - CamHealthCheck 단위 테스트

fixture 는 2026-08-21 보드의 /run/pim-camera/gstApp.json 실측 구조를 따른다.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from checks.cam_health import CamHealthCheck

BOOT_ID = "fb6b4830-783d-48ba-8e31-8910be711078"


def _snapshot(observed_ms=2122000, boot_id=BOOT_ID, statuses=("OK", "OK")):
    return json.dumps({
        "schema": 1,
        "producer": "gstApp",
        "boot_id": boot_id,
        "pid": 613,
        "sequence": 2087,
        "observed_monotonic_ms": observed_ms,
        "observations": [
            {"block": "gstreamer",
             "scope": {"kind": "channel", "id": "ch0", "channels": [0]},
             "status": statuses[0], "code": "NONE", "count": 0,
             "evidence": [{"name": "enc_queue_input", "source": "gstApp",
                           "value": 31274}]},
            {"block": "recording",
             "scope": {"kind": "channel", "id": "ch0", "channels": [0]},
             "status": statuses[1],
             "code": "NONE" if statuses[1] != "FAIL" else "RECORDING_NO_GROWTH",
             "count": 0, "evidence": []},
        ],
    })


CONFIG = {"cam_health": {"path": "/run/pim-camera/gstApp.json", "stale_ms": 5000}}


def _data(raw, uptime_sec=2123.0, boot_id=BOOT_ID):
    return {"raw": raw, "uptime_raw": f"{uptime_sec} 8000.0", "boot_id": boot_id}


class TestCamHealthCollect(unittest.TestCase):
    def test_collect_reads_snapshot_uptime_bootid(self):
        check = CamHealthCheck()
        ssh = MagicMock()

        def side_effect(cmd):
            if "gstApp.json" in cmd:
                return _snapshot()
            if "/proc/uptime" in cmd:
                return "2123.45 8000.00"
            if "boot_id" in cmd:
                return BOOT_ID + "\n"
            return None

        ssh.run.side_effect = side_effect
        data = check.collect(ssh, CONFIG)
        self.assertIn("gstApp", data["raw"])
        self.assertEqual(data["boot_id"], BOOT_ID)

    def test_collect_skips_without_path(self):
        check = CamHealthCheck()
        ssh = MagicMock()
        data = check.collect(ssh, {"cam_health": {"path": None}})
        self.assertTrue(data["skipped"])
        ssh.run.assert_not_called()


class TestCamHealthValidate(unittest.TestCase):
    def setUp(self):
        self.check = CamHealthCheck()

    def test_pass_on_fresh_snapshot(self):
        # observed 2122000ms, uptime 2123s → age 1000ms < 5000ms.
        passed, reason = self.check.validate(_data(_snapshot()), CONFIG)
        self.assertTrue(passed, reason)
        self.assertIn("2 observations", reason)

    def test_skipped_passes(self):
        passed, reason = self.check.validate({"skipped": True}, CONFIG)
        self.assertTrue(passed)
        self.assertIn("Skipped", reason)

    def test_fail_when_file_missing(self):
        passed, reason = self.check.validate(_data(None), CONFIG)
        self.assertFalse(passed)
        self.assertIn("not found", reason)

    def test_fail_on_stale_snapshot(self):
        # observed 2122000ms, uptime 2200s → age 78000ms > 5000ms.
        passed, reason = self.check.validate(
            _data(_snapshot(), uptime_sec=2200.0), CONFIG)
        self.assertFalse(passed)
        self.assertIn("stale", reason)

    def test_future_skew_is_fresh(self):
        # mid-read 발행(observed > uptime)은 fresh 취급 (producer 계약).
        passed, reason = self.check.validate(
            _data(_snapshot(observed_ms=2124000)), CONFIG)
        self.assertTrue(passed, reason)

    def test_fail_on_boot_id_mismatch(self):
        passed, reason = self.check.validate(
            _data(_snapshot(boot_id="00000000-dead-beef-0000-000000000000")),
            CONFIG)
        self.assertFalse(passed)
        self.assertIn("boot_id mismatch", reason)

    def test_fail_on_fail_observation(self):
        passed, reason = self.check.validate(
            _data(_snapshot(statuses=("OK", "FAIL"))), CONFIG)
        self.assertFalse(passed)
        self.assertIn("recording/ch0:RECORDING_NO_GROWTH", reason)

    def test_starting_status_is_not_failure(self):
        passed, reason = self.check.validate(
            _data(_snapshot(statuses=("STARTING", "N/A"))), CONFIG)
        self.assertTrue(passed, reason)

    def test_fail_on_invalid_json(self):
        passed, reason = self.check.validate(_data("{broken"), CONFIG)
        self.assertFalse(passed)
        self.assertIn("not valid JSON", reason)

    # --- 리뷰 반영 회귀 테스트 (경계 조건에서 예외 유출/오판 금지) ---

    def test_json_array_is_fail_not_raise(self):
        """top-level 이 dict 가 아니면 AttributeError 대신 FAIL (엔진은 SSH 예외만 잡음)."""
        passed, reason = self.check.validate(_data("[1, 2, 3]"), CONFIG)
        self.assertFalse(passed)
        self.assertIn("not a JSON object", reason)

    def test_observations_not_list_is_fail_not_raise(self):
        snap = json.loads(_snapshot())
        snap["observations"] = {"oops": 1}
        passed, reason = self.check.validate(_data(json.dumps(snap)), CONFIG)
        self.assertFalse(passed)
        self.assertIn("observations missing or not a list", reason)

    def test_malformed_observation_entries_are_fail_not_raise(self):
        snap = json.loads(_snapshot())
        snap["observations"] = ["not-a-dict", 42]
        passed, reason = self.check.validate(_data(json.dumps(snap)), CONFIG)
        self.assertFalse(passed)
        self.assertIn("malformed observation", reason)

    def test_large_future_skew_is_fail(self):
        """boot_id 로 못 거른 이전 부팅 잔존(관측치가 크게 미래)은 fresh 가 아니다."""
        snap = json.loads(_snapshot(observed_ms=2122000 + 3_600_000))
        del snap["boot_id"]  # boot_id 방어선이 없는 상황을 재현
        passed, reason = self.check.validate(_data(json.dumps(snap)), CONFIG)
        self.assertFalse(passed)
        self.assertIn("future", reason)

    def test_uptime_unreadable_surfaces_unverified(self):
        """기준 시계를 못 읽으면 신선도 검사가 조용히 증발하지 않는다."""
        data = _data(_snapshot())
        data["uptime_raw"] = None
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("freshness not verified", reason)

    def test_missing_file_within_grace_is_stabilization(self):
        """부팅 직후(early_boot_grace_sec 이내) 파일 부재는 stabilization 신호."""
        from verify_retry import is_stabilization_reason
        passed, reason = self.check.validate(
            _data(None, uptime_sec=60.0), CONFIG)
        self.assertFalse(passed)
        self.assertIn("NEED_PRODUCER_SNAPSHOT", reason)
        self.assertTrue(is_stabilization_reason(reason))

    def test_missing_file_beyond_grace_is_hard_fail(self):
        from verify_retry import is_stabilization_reason
        passed, reason = self.check.validate(
            _data(None, uptime_sec=2123.0), CONFIG)
        self.assertFalse(passed)
        self.assertIn("not found", reason)
        self.assertFalse(is_stabilization_reason(reason))

    def test_string_stale_ms_config_is_tolerated(self):
        cfg = {"cam_health": {"path": "/run/pim-camera/gstApp.json",
                              "stale_ms": "5000"}}
        passed, reason = self.check.validate(_data(_snapshot()), cfg)
        self.assertTrue(passed, reason)


if __name__ == "__main__":
    unittest.main()
