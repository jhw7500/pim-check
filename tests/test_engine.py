from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch


from ssh import SshConnectionError, SshTimeoutError


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
        # 8 기본 체크 + cam_health + max9296_abi (2026-08 배포 조합 sync).
        assert len(results) == 10
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

    def test_snapshot_excludes_hardware_evidence_checks_but_registry_exposes_them(self):
        """A hardware collector must never make ordinary health snapshots fail."""
        from checks import checks_for_scope
        from engine import Engine

        engine = Engine(self.ssh, self.profile)

        assert {check.name for check in engine.checks}.isdisjoint({
            "target_identity", "bps_evidence", "mixed_combo_evidence",
        })
        assert {check.name for check in checks_for_scope("hardware_evidence")} == {
            "target_identity", "bps_evidence", "mixed_combo_evidence",
        }

    @patch("engine.time.sleep")
    def test_run_monitor_until_pass_exits_on_clean_snapshot(self, mock_sleep):
        # finalize-aware: 첫 '전 체크 통과' 스냅샷에서 조기 종료한다.
        from engine import Engine

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 100, "interval_sec": 1}  # 큰 상한
        engine = Engine(self.ssh, profile)
        fail_snap = [{"name": "recording", "passed": False, "reason": "NEED_2_FINALIZES"}]
        clean_snap = [{"name": "recording", "passed": True, "reason": "OK"}]
        engine.run_snapshot = MagicMock(side_effect=[fail_snap, clean_snap])

        results, collected, total = engine.run_monitor(until_pass=True)

        # 2번째(통과) 스냅샷에서 종료 — 100s 상한을 끝까지 기다리지 않는다.
        assert engine.run_snapshot.call_count == 2
        assert collected == 2
        # 통과 스냅샷만 반환 — 직전 NEED_2_FINALIZES 일시 fail 은 merge 되지 않는다.
        assert all(r["passed"] for r in results)
        assert results == clean_snap

    @patch("engine.time.sleep")
    def test_run_monitor_stable_fail_early_exit(self, mock_sleep):
        # 동일한 '실제 fail'(비-stabilization)이 STABLE_FAIL_SAMPLES(3)회 연속이면 조기 종료.
        from engine import Engine, STABLE_FAIL_SAMPLES

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 20, "interval_sec": 1}  # samples_total=20
        engine = Engine(self.ssh, profile)
        fail_snap = [{"name": "custom_commands", "passed": False,
                      "reason": "ch3 bitrate (got: FAIL:5596kbps_ex=8192kbps)"}]
        engine.run_snapshot = MagicMock(return_value=fail_snap)  # 매번 같은 실제 fail

        results, collected, total = engine.run_monitor(until_pass=True)

        assert engine.run_snapshot.call_count == STABLE_FAIL_SAMPLES  # 20 이 아니라 3
        assert collected == STABLE_FAIL_SAMPLES
        assert any(not r["passed"] for r in results)

    @patch("engine.time.sleep")
    def test_run_monitor_stable_fail_with_varying_reason(self, mock_sleep):
        # reason 의 측정값(숫자)만 sample 마다 흔들리면 정규화 후 동일 시그니처 → 조기 종료.
        from engine import Engine, STABLE_FAIL_SAMPLES

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 20, "interval_sec": 1}
        engine = Engine(self.ssh, profile)
        kbps = itertools.count(5596)  # STABLE_FAIL_SAMPLES 가 커져도 StopIteration 없음
        engine.run_snapshot = MagicMock(side_effect=lambda *a, **k: [
            {"name": "custom_commands", "passed": False,
             "reason": f"ch3 bitrate (got: FAIL:{next(kbps)}kbps_ex=8192kbps)"}])

        results, collected, total = engine.run_monitor(until_pass=True)

        # 숫자만 달라지므로 정규화 시그니처가 같아 3 회 연속에서 끊긴다(이전엔 20 까지 갔음).
        assert engine.run_snapshot.call_count == STABLE_FAIL_SAMPLES
        assert collected == STABLE_FAIL_SAMPLES
        assert any(not r["passed"] for r in results)

    @patch("engine.time.sleep")
    def test_run_monitor_changing_failure_kind_keeps_sampling(self, mock_sleep):
        # 같은 집계 체크(custom_commands)라도 '실패 종류'(reason 텍스트)가 sample 마다
        # 다르면(수렴 중 다른 sub-command fail) 시그니처가 달라 조기 종료하지 않는다.
        # name-only 였다면 3 회에서 false 종료했을 시나리오 (PR #27 codex 지적).
        from engine import STABLE_FAIL_SAMPLES, Engine

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 5, "interval_sec": 1}  # samples_total=5
        engine = Engine(self.ssh, profile)
        subcmds = itertools.cycle(["fps check", "bps check", "i2c check"])
        engine.run_snapshot = MagicMock(side_effect=lambda *a, **k: [
            {"name": "custom_commands", "passed": False,
             "reason": f"{next(subcmds)}: mismatch"}])  # 종류가 매번 다름(숫자 무관)

        results, collected, total = engine.run_monitor(until_pass=True)

        # streak 임계를 넘겨 끝까지 샘플링했음을 self-documenting 하게 검증.
        assert total > STABLE_FAIL_SAMPLES
        assert collected == total

    @patch("engine.time.sleep")
    def test_run_monitor_varying_channel_keeps_sampling(self, mock_sleep):
        # 측정값만 마스킹하므로 채널번호(chN)는 보존된다. 수렴 중 ch1↔ch3 가 번갈아
        # 실패하면(측정값도 변동) 시그니처가 달라 조기 종료하지 않는다 (PR #29 claude 지적).
        from engine import STABLE_FAIL_SAMPLES, Engine

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 5, "interval_sec": 1}  # samples_total=5
        engine = Engine(self.ssh, profile)
        # 다중 자리 채널(ch10/ch11)로 lookbehind 가 뒷자리만 마스킹하는 버그도 함께 검증.
        chans = itertools.cycle(["ch10", "ch11"])
        kbps = itertools.count(5500)
        engine.run_snapshot = MagicMock(side_effect=lambda *a, **k: [
            {"name": "custom_commands", "passed": False,
             "reason": f"{next(chans)} bitrate (got: {next(kbps)}kbps)"}])

        results, collected, total = engine.run_monitor(until_pass=True)

        # ch1/ch3 가 구분되므로(측정값만 #) 3 회에서 끊기지 않고 끝까지 간다.
        assert total > STABLE_FAIL_SAMPLES
        assert collected == total

    @patch("engine.time.sleep")
    def test_run_monitor_stabilization_fail_does_not_early_exit(self, mock_sleep):
        # NEED_2_FINALIZES('준비 중')는 stable-fail 대상이 아니므로 끝까지 샘플링한다.
        from engine import Engine

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 5, "interval_sec": 1}  # samples_total=5
        engine = Engine(self.ssh, profile)
        pending_snap = [{"name": "recording", "passed": False,
                         "reason": "FAIL:NEED_2_FINALIZES_AFTER_BOOT"}]
        engine.run_snapshot = MagicMock(return_value=pending_snap)

        results, collected, total = engine.run_monitor(until_pass=True)

        # 3 회에 끊기지 않고 samples_total(5)까지 간다.
        assert collected == 5

    @patch("engine.time.sleep")
    def test_run_monitor_without_until_pass_merges_transient_fail(self, mock_sleep):
        # 기본(comprehensive): until_pass 없으면 전 구간 sample → 일시 fail 이 merge 되어 살아남는다.
        from engine import Engine

        profile = dict(self.profile)
        profile["monitor"] = {"duration_sec": 2, "interval_sec": 1}  # samples_total=2
        engine = Engine(self.ssh, profile)
        fail_snap = [{"name": "recording", "passed": False, "reason": "NEED_2_FINALIZES"}]
        clean_snap = [{"name": "recording", "passed": True, "reason": "OK"}]
        engine.run_snapshot = MagicMock(side_effect=[fail_snap, clean_snap])

        results, collected, total = engine.run_monitor(until_pass=False)

        assert collected == 2
        # 직전 fail 이 merge 로 살아남는다(지속검증 의미 — 한 번이라도 fail 이면 fail).
        assert any(not r["passed"] for r in results)

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

    @patch("engine.time.sleep")
    def test_snapshot_retry_on_ssh_error_then_success(self, mock_sleep):
        """개별 체크 SSH 에러 → 재시도 → 성공"""
        from engine import Engine

        call_count = [0]

        def mock_run(cmd):
            call_count[0] += 1
            if call_count[0] == 1:
                raise SshConnectionError("first fail")
            return None

        self.ssh.run = mock_run
        engine = Engine(self.ssh, self.profile)
        results = engine.run_snapshot(retries=1)

        # SSH 에러가 재시도로 복구되어 SSH_ERROR가 아닌 결과
        assert any(not r["reason"].startswith("SSH_ERROR") for r in results)

    @patch("engine.time.sleep")
    def test_snapshot_retry_exhausted(self, mock_sleep):
        """개별 체크 SSH 에러 → 재시도 소진 → SSH_ERROR 기록"""
        from engine import Engine

        self.ssh.run = MagicMock(side_effect=SshTimeoutError("timeout"))
        engine = Engine(self.ssh, self.profile)
        results = engine.run_snapshot(retries=1)

        # SSH를 호출하는 체크만 SSH_ERROR 발생
        ssh_error_results = [r for r in results if "SSH_ERROR" in r.get("reason", "")]
        assert len(ssh_error_results) > 0
        for r in ssh_error_results:
            assert r["passed"] is False
        # 재시도 sleep이 호출되었는지 확인
        assert mock_sleep.call_count >= 1
