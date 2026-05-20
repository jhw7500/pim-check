"""tests/test_stabilize_ready.py - 단계별 readiness 게이트 (stabilize 대체).

고정 sleep(stabilize_sec) 대신 단계별 조건을 폴링해, 준비되면 즉시 진행하고
안 되면 timeout까지 재시도하는 wait_until_ready 드라이버 + 1차(SSH) 단계 검증.
시간 의존을 제거하기 위해 _sleep / _clock 을 주입한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from setup import SetupManager, READINESS_POLL_INTERVAL


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
            order.append("s1"); return True
        def s2():
            order.append("s2"); return True
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
