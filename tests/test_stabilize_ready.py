"""tests/test_stabilize_ready.py - 단계별 readiness 게이트 (stabilize 대체).

고정 sleep(stabilize_sec) 대신 단계별 조건을 폴링해, 준비되면 즉시 진행하고
안 되면 timeout까지 재시도하는 wait_until_ready 드라이버 + 1차(SSH) 단계 검증.
시간 의존을 제거하기 위해 _sleep / _clock 을 주입한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from setup import (
    SetupManager,
    READINESS_POLL_INTERVAL,
    FSYNC_SETTLE_SEC,
    profile_is_camera,
)


def _mgr():
    return SetupManager(MagicMock(), reboot_timeout=300, poll_interval=10)


class _Clock:
    """호출마다 step 초 증가하는 가짜 단조 시계."""
    def __init__(self, step=10.0):
        self.t = 0.0
        self.step = step
    def __call__(self):
        v = self.t
        self.t += self.step
        return v


class TestWaitUntilReadyDriver:
    def test_returns_true_after_debounce_consecutive_passes(self):
        mgr = _mgr()
        calls = {"n": 0}
        def pred():
            calls["n"] += 1
            return True
        ok = mgr.wait_until_ready(
            [("always", pred)], poll_interval=10, debounce=2, timeout=260,
            _sleep=lambda s: None, _clock=_Clock(),
        )
        assert ok is True
        # 연속 2회(debounce) 충족이면 즉시 통과 — 2회만 평가.
        assert calls["n"] == 2

    def test_debounce_resets_on_flap(self):
        mgr = _mgr()
        seq = iter([True, False, True, True])  # 흔들림 후 연속 2회
        ok = mgr.wait_until_ready(
            [("flappy", lambda: next(seq))], poll_interval=10, debounce=2,
            timeout=260, _sleep=lambda s: None, _clock=_Clock(),
        )
        assert ok is True

    def test_timeout_returns_false_when_never_ready(self):
        mgr = _mgr()
        ok = mgr.wait_until_ready(
            [("never", lambda: False)], poll_interval=10, debounce=2,
            timeout=50, _sleep=lambda s: None, _clock=_Clock(step=10.0),
        )
        assert ok is False

    def test_stages_run_in_order_and_all_must_pass(self):
        mgr = _mgr()
        order = []
        def s1():
            order.append("s1")
            return True
        def s2():
            order.append("s2")
            return True
        ok = mgr.wait_until_ready(
            [("s1", s1), ("s2", s2)], poll_interval=10, debounce=1,
            timeout=260, _sleep=lambda s: None, _clock=_Clock(),
        )
        assert ok is True
        # s1 이 먼저 통과한 뒤에야 s2 가 평가된다.
        assert order[0] == "s1"
        assert "s2" in order


class TestStage1Ssh:
    def test_ready_ssh_true_when_connected(self):
        mgr = _mgr()
        mgr.ssh.check_connectivity.return_value = True
        assert mgr._ready_ssh() is True

    def test_ready_ssh_false_when_down(self):
        mgr = _mgr()
        mgr.ssh.check_connectivity.return_value = False
        assert mgr._ready_ssh() is False


class TestStage2Processes:
    def test_all_processes_up_is_ready(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "1234"  # pgrep 가 PID 반환
        assert mgr._ready_processes(["gstApp", "BG_Check_for_pim"]) is True

    def test_missing_process_is_not_ready(self):
        mgr = _mgr()
        # gstApp 만 떠 있고 나머지는 없음 (pgrep -x, -f 모두 빈 결과)
        def run(cmd, *a, **k):
            return "1234" if "gstApp" in cmd else None
        mgr.ssh.run.side_effect = run
        assert mgr._ready_processes(["gstApp", "chk_cam_operate"]) is False

    def test_pgrep_f_fallback_when_x_misses(self):
        mgr = _mgr()
        # pgrep -x 는 빈 결과, pgrep -f 가 매칭 (셸 스크립트형 프로세스)
        def run(cmd, *a, **k):
            return "999" if cmd.startswith("pgrep -f") else None
        mgr.ssh.run.side_effect = run
        assert mgr._ready_processes(["chk_cam_operate"]) is True

    def test_stage_added_only_when_processes_injected(self):
        mgr = _mgr()
        # 주입 전: ssh 단계만
        assert [n for n, _ in mgr._stabilize_stages()] == ["ssh"]
        # run_setup(ready_processes=...) 주입 후: ssh + processes
        mgr._ready_processes_list = ["gstApp"]
        assert [n for n, _ in mgr._stabilize_stages()] == ["ssh", "processes"]


class TestReadinessPollInterval:
    def test_stabilize_uses_short_readiness_interval_not_recovery_poll(self):
        # 리부트 복구 폴링은 60초지만, readiness 디바운스는 짧은 간격을 써야 한다.
        mgr = SetupManager(MagicMock(), reboot_timeout=300, poll_interval=60)
        captured = {}

        def fake_wait(stages, *, poll_interval, debounce, timeout, **kw):
            captured.update(poll_interval=poll_interval, timeout=timeout,
                            debounce=debounce)
            return True

        mgr.wait_until_ready = fake_wait
        mgr._stabilize(260)
        assert captured["poll_interval"] == READINESS_POLL_INTERVAL
        assert captured["poll_interval"] < mgr.poll_interval  # 복구 폴링보다 짧음
        assert captured["timeout"] == 260  # 전체 예산은 stabilize_sec 유지

    def test_readiness_interval_is_overridable(self):
        mgr = SetupManager(MagicMock(), poll_interval=60)
        mgr.readiness_poll_interval = 3
        captured = {}
        mgr.wait_until_ready = lambda stages, *, poll_interval, **kw: (
            captured.update(pi=poll_interval) or True)
        mgr._stabilize(100)
        assert captured["pi"] == 3


class TestStage3Recording:
    def test_recent_recording_file_is_ready(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "/dev/shm/2026-ch0.mp4.part"  # find 가 파일 반환
        assert mgr._ready_recording(["/dev/shm", "/mnt/sd_cam"]) is True

    def test_no_recent_file_is_not_ready(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = ""  # find 결과 없음
        assert mgr._ready_recording(["/dev/shm"]) is False

    def test_find_command_covers_paths_and_patterns(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "x.part"
        mgr._ready_recording(["/dev/shm", "/dev/shm/recording", "/mnt/sd_cam"])
        cmd = mgr.ssh.run.call_args[0][0]
        assert "find /dev/shm /dev/shm/recording /mnt/sd_cam" in cmd
        for pat in ("*.part", "*.srt", "*.mp4", "*.ts"):
            assert pat in cmd
        assert "-mmin -2" in cmd

    def test_empty_paths_is_not_ready(self):
        mgr = _mgr()
        assert mgr._ready_recording([]) is False

    def test_recording_stage_added_when_paths_injected(self):
        mgr = _mgr()
        mgr._ready_processes_list = ["gstApp"]
        mgr._ready_recording_paths = ["/dev/shm", "/mnt/sd_cam"]
        assert [n for n, _ in mgr._stabilize_stages()] == ["ssh", "processes", "recording"]


class _FixedClock:
    """수동으로 값을 세팅하는 가짜 monotonic 시계."""
    def __init__(self, start=0.0):
        self.v = start
    def __call__(self):
        return self.v


class TestStageCameraInitFsync:
    """카메라 init readiness — dmesg max9296_fsync fps 로그 + settle."""

    def test_not_ready_when_log_absent(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "0"  # grep -c → 0건
        assert mgr._ready_dmesg_fsync(_clock=_FixedClock()) is False

    def test_settle_requires_elapsed_time(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "1"  # 로그 1건 존재
        clk = _FixedClock(start=100.0)
        # 최초 관측: settle 0초 → 아직 무효
        assert mgr._ready_dmesg_fsync(_clock=clk) is False
        # settle 직전 → 여전히 무효
        clk.v = 100.0 + FSYNC_SETTLE_SEC - 0.5
        assert mgr._ready_dmesg_fsync(_clock=clk) is False
        # settle 충족 → 유효
        clk.v = 100.0 + FSYNC_SETTLE_SEC
        assert mgr._ready_dmesg_fsync(_clock=clk) is True

    def test_log_disappear_resets_settle_timer(self):
        mgr = _mgr()
        seq = iter(["1", "0", "1"])
        mgr.ssh.run.side_effect = lambda *a, **k: next(seq)
        clk = _FixedClock(start=0.0)
        mgr._ready_dmesg_fsync(_clock=clk)            # seen_at=0
        clk.v = 10.0
        assert mgr._ready_dmesg_fsync(_clock=clk) is False  # "0" → 리셋
        assert mgr._fsync_seen_at is None
        # 재출현: seen_at 새로 기록, settle 미경과 → False
        assert mgr._ready_dmesg_fsync(_clock=clk) is False
        assert mgr._fsync_seen_at == 10.0

    def test_ssh_error_is_not_ready_and_resets(self):
        mgr = _mgr()
        mgr._fsync_seen_at = 5.0
        mgr.ssh.run.side_effect = RuntimeError("boom")
        assert mgr._ready_dmesg_fsync() is False
        assert mgr._fsync_seen_at is None

    def test_grep_command_uses_marker(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "0"
        mgr._ready_dmesg_fsync(_clock=_FixedClock())
        cmd = mgr.ssh.run.call_args[0][0]
        assert "dmesg" in cmd
        assert "max9296_fsync fps :" in cmd
        assert "grep -c" in cmd

    def test_camera_init_stage_before_recording_when_enabled(self):
        mgr = _mgr()
        mgr._ready_processes_list = ["gstApp"]
        mgr._ready_fsync = True
        mgr._ready_recording_paths = ["/dev/shm", "/mnt/sd_cam"]
        assert [n for n, _ in mgr._stabilize_stages()] == [
            "ssh", "processes", "camera_init", "recording"]

    def test_camera_init_stage_skipped_when_not_camera(self):
        mgr = _mgr()
        mgr._ready_recording_paths = ["/dev/shm"]
        # ready_fsync 미주입(기본 False) → camera_init 단계 없음
        assert [n for n, _ in mgr._stabilize_stages()] == ["ssh", "recording"]

    def test_run_setup_stores_ready_fsync(self):
        mgr = _mgr()
        # edge_changes 없고 inject 없으면 run_setup 은 곧 False 반환하지만
        # 그 전에 readiness 주입값은 저장된다.
        mgr.run_setup({}, ready_fsync=True)
        assert mgr._ready_fsync is True
        assert mgr._fsync_seen_at is None


class TestProfileIsCamera:
    def test_camera_when_fsync_check_present(self):
        prof = {"checks": {"custom_commands": [
            {"name": "dmesg max9296_fsync fps",
             "command": "dmesg | grep -oE 'max9296_fsync fps : [0-9]+'"}]}}
        assert profile_is_camera(prof) is True

    def test_not_camera_without_fsync_check(self):
        prof = {"checks": {"custom_commands": [
            {"name": "config", "command": "jq . /root/shared_v/edgeconf_pim.json"}]}}
        assert profile_is_camera(prof) is False

    def test_not_camera_when_no_checks(self):
        assert profile_is_camera({}) is False
        assert profile_is_camera({"checks": None}) is False
        assert profile_is_camera({"checks": {}}) is False
