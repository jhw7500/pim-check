from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


PROFILE = {
    "monitor": {"duration_sec": 2, "interval_sec": 1},
    "checks": {
        "processes": {"required": ["gstApp"], "optional": []},
        "cpu": {"bg_check_max_pct": 3.0, "gst_range": [0, 100]},
        "cam_state": {
            "dir": "/tmp/cam_state",
            "valid_states": ["healthy"],
            "expected_state": "healthy",
            "max_streak": 0,
        },
        "legacy_files": {"must_not_exist": []},
        "thermal": {"max_temp_c": 85, "warn_temp_c": 80},
        "jq": {"max_forks_per_sample": 2},
        "logs": {"error_patterns": ["kernel panic"]},
        "recording": {"expected_channels": None, "session_progress": None},
    },
}


class TestEngine:
    def setup_method(self):
        self.ssh = MagicMock()
        self.profile = PROFILE

    def test_run_single_snapshot(self):
        from engine import Engine

        self.ssh.run = MagicMock(return_value=None)
        engine = Engine(self.ssh, self.profile)
        results = engine.run_snapshot()

        assert isinstance(results, list)
        assert len(results) == 8
        for entry in results:
            assert "name" in entry
            assert "passed" in entry
            assert "reason" in entry

    def test_get_all_checks_registered(self):
        from engine import Engine

        engine = Engine(self.ssh, self.profile)
        names = [c.name for c in engine.checks]

        assert "process" in names
        assert "cam_state" in names
        assert "legacy_files" in names
        assert "thermal" in names
        assert "jq_forks" in names
        assert "logs" in names
        assert "recording" in names

    @patch("engine.time.sleep")
    def test_run_monitor_loop(self, mock_sleep):
        from engine import Engine

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 3, "interval_sec": 1}
        self.ssh.run = MagicMock(return_value=None)
        engine = Engine(self.ssh, profile)
        results, collected, total = engine.run_monitor()

        assert isinstance(results, list)
        assert isinstance(collected, int)
        assert isinstance(total, int)
        assert collected >= 1
        assert total >= 1

    def test_worst_results_across_snapshots(self):
        from engine import Engine

        engine = Engine(self.ssh, self.profile)

        snapshot_pass = [
            {"name": "thermal", "passed": True, "reason": "OK", "data": {}},
        ]
        snapshot_fail = [
            {"name": "thermal", "passed": False, "reason": "Temperature 90 > max 85", "data": {}},
        ]

        merged = engine.merge_snapshots([snapshot_pass, snapshot_fail])

        thermal = next(r for r in merged if r["name"] == "thermal")
        assert thermal["passed"] is False
        assert "90" in thermal["reason"]

    @patch("engine.time.sleep")
    def test_thermal_shutdown_recovery(self, mock_sleep):
        """모니터링 중 SSH 끊김 → 복귀 대기 → 계속 수집"""
        from engine import Engine

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 10, "interval_sec": 1}

        call_count = [0]

        def mock_connectivity():
            call_count[0] += 1
            # 처음 2번 False (shutdown 감지) → 3번째 True (복귀)
            if call_count[0] <= 2:
                return False
            return True

        self.ssh.check_connectivity = mock_connectivity
        self.ssh.run = MagicMock(return_value=None)

        engine = Engine(self.ssh, profile)
        results, collected, total = engine.run_monitor()

        # 복귀 후 최소 1개 스냅샷 수집
        assert collected >= 1
        assert isinstance(results, list)

    @patch("engine.time.sleep")
    def test_thermal_shutdown_timeout(self, mock_sleep):
        """모니터링 중 SSH 끊김 → 복귀 안 됨 → 수집된 것으로 리포트"""
        from engine import Engine

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 5, "interval_sec": 1}

        self.ssh.check_connectivity = MagicMock(return_value=False)
        self.ssh.run = MagicMock(return_value=None)

        engine = Engine(self.ssh, profile)
        results, collected, total = engine.run_monitor()

        # 타겟 복귀 못 했으므로 0개 수집
        assert collected == 0
        assert results == []
