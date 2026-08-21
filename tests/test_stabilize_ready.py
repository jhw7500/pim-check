"""tests/test_stabilize_ready.py - 단계별 readiness 게이트 (stabilize 대체).

고정 sleep(stabilize_sec) 대신 단계별 조건을 폴링해, 준비되면 즉시 진행하고
안 되면 timeout까지 재시도하는 wait_until_ready 드라이버 + 1차(SSH) 단계 검증.
시간 의존을 제거하기 위해 _sleep / _clock 을 주입한다.
"""
from __future__ import annotations

import re

from unittest.mock import MagicMock

from setup import (
    SetupManager,
    READINESS_POLL_INTERVAL,
    FSYNC_MARKER_RE,
    FSYNC_SETTLE_SEC,
    AE_SETTLE_MATCH_GAP_SEC,
    AE_SETTLE_GSTAPP_ETIME_SEC,
    ISP_SINGLE_ADDR,
    SESSION_ANCHOR_PATH,
    ISP_DUAL_CH_ADDRS,
    ae_settle_targets,
    profile_is_camera,
    readiness_kwargs,
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
        mgr.ssh.run.return_value = "t=0 p=0 n=0"
        assert mgr._ready_dmesg_fsync(_clock=_FixedClock()) is False

    def test_settle_requires_elapsed_time(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "t=1 p=1 n=1"  # 앵커 이후 로그 1건
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
        seq = iter(["t=1 p=1 n=1", "t=0 p=0 n=0", "t=1 p=1 n=1"])
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

    def test_probe_command_counts_marker_and_anchor_delta(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "t=0 p=0 n=0"
        mgr._ready_dmesg_fsync(_clock=_FixedClock())
        cmd = mgr.ssh.run.call_args[0][0]
        assert "dmesg" in cmd
        assert FSYNC_MARKER_RE in cmd
        # awk 단일 패스 — 총건수/타임스탬프파싱/앵커이후 세 숫자를 항상 출력한다.
        # (grep -c 는 0건일 때 exit 1 이라 ssh.run 이 None 을 반환 — 그 규약에
        #  의존하지 않도록 END 에서 무조건 찍는다.)
        assert "awk -v a=" in cmd
        assert 't=%d p=%d n=%d' in cmd
        # 앵커가 명령에 실려야 델타 판정이 성립한다.
        mgr._dmesg_anchor_uptime = 461.7
        mgr._ready_dmesg_fsync(_clock=_FixedClock())
        assert "awk -v a=461.7" in mgr.ssh.run.call_args[0][0]

    def test_anchor_delta_blocks_stale_pre_anchor_lines(self):
        """하드리셋 시나리오 — 링버퍼에 직전 부팅 fsync 가 남아도 게이트는 안 열린다.

        재부팅은 dmesg 링버퍼를 비우지만 하드리셋(SoC 재부팅 없음)은 비우지 않는다.
        총건수(t)만 보면 조기 개방되고, 앵커 이후(n)를 보면 막힌다.
        """
        mgr = _mgr()
        mgr._dmesg_anchor_uptime = 500.0
        mgr.ssh.run.return_value = "t=3 p=3 n=0"   # 전부 앵커 이전(직전 부팅)
        clk = _FixedClock(start=0.0)
        assert mgr._ready_dmesg_fsync(_clock=clk) is False
        clk.v = 100.0
        assert mgr._ready_dmesg_fsync(_clock=clk) is False
        # 리셋 이후 라인이 생기면 통과한다.
        mgr.ssh.run.return_value = "t=4 p=4 n=1"
        assert mgr._ready_dmesg_fsync(_clock=clk) is False   # 최초 관측
        clk.v = 100.0 + FSYNC_SETTLE_SEC
        assert mgr._ready_dmesg_fsync(_clock=clk) is True

    def test_falls_back_to_total_when_timestamps_unparseable(self):
        """printk 타임스탬프가 꺼진 보드 — 앵커 델타 불가 시 기존 동작으로 폴백.

        게이트가 영영 안 열리는 것보다 '존재만으로 판정'이 낫다.
        """
        mgr = _mgr()
        mgr._dmesg_anchor_uptime = 500.0
        mgr.ssh.run.return_value = "t=2 p=0 n=0"
        clk = _FixedClock(start=0.0)
        assert mgr._ready_dmesg_fsync(_clock=clk) is False   # 최초 관측
        clk.v = FSYNC_SETTLE_SEC
        assert mgr._ready_dmesg_fsync(_clock=clk) is True

    def test_malformed_probe_output_is_not_ready(self):
        mgr = _mgr()
        for out in ("", None, "garbage", "t=x p=y n=z"):
            mgr._fsync_seen_at = None
            mgr.ssh.run.return_value = out
            assert mgr._ready_dmesg_fsync(_clock=_FixedClock()) is False, out

    def test_marker_re_matches_old_and_new_dmesg_formats(self):
        """구형(pre-2.5)과 2.5+ 실측 dmesg 라인을 모두 매칭해야 한다.

        2.5 실측 (2026-08-21 보드):
        '[I2C:1][max9296.c:4619] max9296_fsync side fps : 15, low : 65666, ...'
        """
        pattern = re.compile(FSYNC_MARKER_RE)
        old_line = "[I2C:1][max9296.c:1234] max9296_fsync fps : 30, low : 1"
        new_lines = [
            "[I2C:1][max9296.c:4619] max9296_fsync side fps : 15, low : 65666, high : 1000",
            "[I2C:2][max9296.c:4619] max9296_fsync dual fps : 30, low : 1, high : 2",
            "[I2C:2][max9296.c:4619] max9296_fsync single fps : 15, low : 1, high : 2",
            # 미래의 새 mode 단어도 매칭해야 한다 (open set — 화이트리스트 재파손 방지).
            "[I2C:1][max9296.c:4619] max9296_fsync quad-wide fps : 60, low : 1, high : 2",
        ]
        assert pattern.search(old_line)
        for line in new_lines:
            assert pattern.search(line), line
        # 무관한 라인은 매칭하지 않는다.
        assert not pattern.search("max9296_fsync thread started")

    def test_count_parsed_from_self_exiting_output(self):
        mgr = _mgr()
        # '|| echo 0' 덕에 board 는 0건이어도 None 이 아닌 "0" 을 반환
        mgr.ssh.run.return_value = "0"
        assert mgr._ready_dmesg_fsync(_clock=_FixedClock()) is False
        # None(=SSH 비정상)도 안전하게 0 처리
        mgr.ssh.run.return_value = None
        assert mgr._ready_dmesg_fsync(_clock=_FixedClock()) is False

    def test_camera_init_stage_before_recording_when_enabled(self):
        mgr = _mgr()
        mgr._ready_processes_list = ["gstApp"]
        mgr._ready_fsync = True
        mgr._ready_recording_paths = ["/dev/shm", "/mnt/sd_cam"]
        assert [n for n, _ in mgr._stabilize_stages()] == [
            "ssh", "session_anchor", "processes", "camera_init", "recording"]

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
    """카메라 판정은 setup 설정 기반 (test-step custom_commands 와 분리)."""

    def test_camera_when_edgeconf_enables_channel(self):
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.vflip": False}}}
        assert profile_is_camera(prof) is True

    def test_camera_when_i2c1_channel_enabled(self):
        prof = {"setup": {"edgeconf_changes": {".VHL_CAM.i2c1.ch3.enable": True}}}
        assert profile_is_camera(prof) is True

    def test_not_camera_when_no_channel_enable(self):
        # VHL_CAM 키가 있어도 채널 enable 이 아니면 카메라 아님
        prof = {"setup": {"edgeconf_changes": {".VHL_CAM.cam_width": 1280}}}
        assert profile_is_camera(prof) is False

    def test_not_camera_for_network_config(self):
        prof = {"setup": {"edgeconf_changes": {".NETWORK.wifi.ssid": "x"}}}
        assert profile_is_camera(prof) is False

    def test_channel_enable_false_is_not_camera(self):
        prof = {"setup": {"edgeconf_changes": {".VHL_CAM.i2c2.ch0.enable": False}}}
        assert profile_is_camera(prof) is False

    def test_explicit_key_opt_in_overrides(self):
        # edgeconf 신호가 없어도 명시 키로 켤 수 있다
        prof = {"setup": {"camera_init_required": True, "edgeconf_changes": {}}}
        assert profile_is_camera(prof) is True

    def test_explicit_key_opt_out_overrides_channel_signal(self):
        # 채널 enable 이 있어도 명시 False 면 게이트 off
        prof = {"setup": {"camera_init_required": False,
                          "edgeconf_changes": {".VHL_CAM.i2c2.ch0.enable": True}}}
        assert profile_is_camera(prof) is False

    def test_not_camera_when_no_setup(self):
        assert profile_is_camera({}) is False
        assert profile_is_camera({"setup": None}) is False
        assert profile_is_camera({"setup": {}}) is False

    def test_not_camera_when_profile_is_none(self):
        assert profile_is_camera(None) is False


class TestReadinessKwargs:
    def test_camera_profile_enables_fsync_and_paths(self):
        from setup import readiness_kwargs, RECORDING_DIRS
        prof = {
            "setup": {"edgeconf_changes": {".VHL_CAM.i2c2.ch0.enable": True}},
            "checks": {"processes": {"required": ["gstApp", "chk_cam_operate"]}},
        }
        kw = readiness_kwargs(prof)
        assert kw["ready_processes"] == ["gstApp", "chk_cam_operate"]
        assert kw["ready_recording_paths"] == RECORDING_DIRS
        assert kw["ready_fsync"] is True

    def test_non_camera_profile_disables_fsync(self):
        from setup import readiness_kwargs
        prof = {"setup": {"edgeconf_changes": {".NETWORK.wifi.ssid": "x"}},
                "checks": {"custom_commands": [{"name": "cfg", "command": "jq ."}]}}
        kw = readiness_kwargs(prof)
        assert kw["ready_fsync"] is False
        assert kw["ready_processes"] == []

    def test_handles_missing_checks(self):
        from setup import readiness_kwargs
        kw = readiness_kwargs({})
        assert kw["ready_processes"] == []
        assert kw["ready_fsync"] is False


class TestAeSettleTargets:
    """AE 정착 기대값 산출 — 케이스 edgeconf_changes 단일 출처 (pim-check#61).

    보드 실측(2026-08-21): 콜드 기동 후 AE 레지스터가 최종값에 도달하기까지
    gstApp 기동 +16s(=boot+28s). 그 전엔 전이값(AE_CTRL 0x029c, AE_GAIN 0x0100)이
    읽혀 readback 체크가 오탐한다. 기대값을 케이스에서 유도해 '기대값과 일치하는
    읽기가 3초 이상 간격으로 2회' 관측될 때 정착으로 본다.
    """

    def test_manual_channel_yields_ae_ctrl_and_ae_gain(self):
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
            ".VHL_CAM.i2c2.ch0.ae_gain": 512,
        }}}
        targets = ae_settle_targets(prof)
        # 버스에 채널이 하나뿐 → single 주소 0x3c
        assert [(t["bus"], t["addr"], t["reg"], t["expected"]) for t in targets] == [
            (2, "0x3c", "0x50 0x02", "0x020x90"),   # AE_CTRL manual
            (2, "0x3c", "0x50 0x06", "0x020x00"),   # AE_GAIN 512
        ]

    def test_auto_channel_yields_ae_ctrl_only(self):
        """auto 채널은 gain 이 FW 재량이라 기대값이 없다 — AE_CTRL 만 단언."""
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch1.enable": True,
            ".VHL_CAM.i2c2.ch1.ae_on": True,
            ".VHL_CAM.i2c2.ch1.ae_gain": 256,
        }}}
        targets = ae_settle_targets(prof)
        assert [(t["reg"], t["expected"]) for t in targets] == [
            ("0x50 0x02", "0x020x99")]

    def test_bus_is_derived_from_key_not_channel_number(self):
        """i2c1 → ch2/ch3, i2c2 → ch0/ch1. 버스는 키에서 읽는다(하드코딩 금지)."""
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c1.ch3.enable": True,
            ".VHL_CAM.i2c1.ch3.ae_on": False,
            ".VHL_CAM.i2c1.ch3.ae_gain": 8192,
        }}}
        targets = ae_settle_targets(prof)
        assert all(t["bus"] == 1 for t in targets)
        assert all(t["addr"] == ISP_SINGLE_ADDR for t in targets)  # bus1 단독 채널
        assert targets[1]["expected"] == "0x200x00"   # 8192 = 0x2000

    def test_two_channels_on_same_bus_get_distinct_addresses(self):
        """같은 버스의 두 채널은 서로 다른 i2c 주소로 읽어야 한다.

        회귀 방지 (2026-08-21 보드 실측 적발): 주소를 0x3c 로 고정하면 bus2 의
        ch0/ch1 이 같은 값을 읽어, 한쪽 기대값으로 다른 쪽을 통과시키는 오탐이 난다.
        실측 대조 — bus2 @0x11=ch0(manual 0x0290/gain 0x0200),
        @0x12=ch1(auto 0x0299/gain 0x0100) 로 edgeconf 와 정확히 일치.
        """
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
            ".VHL_CAM.i2c2.ch0.ae_gain": 512,
            ".VHL_CAM.i2c2.ch1.enable": True,
            ".VHL_CAM.i2c2.ch1.ae_on": True,
        }}}
        targets = ae_settle_targets(prof)
        by_label = {t["label"]: t for t in targets}
        assert by_label["ch0 AE_CTRL"]["addr"] == ISP_DUAL_CH_ADDRS[0]
        assert by_label["ch1 AE_CTRL"]["addr"] == ISP_DUAL_CH_ADDRS[1]
        # 같은 버스지만 주소가 달라야 두 채널이 구분된다.
        assert by_label["ch0 AE_CTRL"]["bus"] == by_label["ch1 AE_CTRL"]["bus"] == 2
        assert len({t["addr"] for t in targets}) == 2

    def test_dual_addr_mapping_is_by_channel_parity_on_each_bus(self):
        """버스마다 2채널(dual)이면 짝수→0x11, 홀수→0x12 (보드 실측 4ch)."""
        edge = {}
        for bus, chs in ((2, (0, 1)), (1, (2, 3))):
            for ch in chs:
                edge[f".VHL_CAM.i2c{bus}.ch{ch}.enable"] = True
                edge[f".VHL_CAM.i2c{bus}.ch{ch}.ae_on"] = True
        targets = ae_settle_targets({"setup": {"edgeconf_changes": edge}})
        got = {t["label"]: (t["bus"], t["addr"]) for t in targets}
        assert got == {
            "ch0 AE_CTRL": (2, ISP_DUAL_CH_ADDRS[0]),
            "ch1 AE_CTRL": (2, ISP_DUAL_CH_ADDRS[1]),
            "ch2 AE_CTRL": (1, ISP_DUAL_CH_ADDRS[0]),
            "ch3 AE_CTRL": (1, ISP_DUAL_CH_ADDRS[1]),
        }

    def test_single_channel_on_bus_uses_broadcast_address(self):
        """버스에 채널이 하나면 0x3c — 드라이버가 `dual ? CH_ADDR : 0x3c` 로 분기한다.

        코퍼스 근거: 프로파일 readback 249건에서 버스당 1채널은 전부 0x3c(129건).
        """
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch1.enable": True,
            ".VHL_CAM.i2c2.ch1.ae_on": True,
        }}}
        assert [t["addr"] for t in ae_settle_targets(prof)] == [ISP_SINGLE_ADDR]

    def test_address_branch_is_per_bus_not_per_case(self):
        """총 2채널이라도 **버스당 1채널**이면 양쪽 다 0x3c.

        판별 케이스: ch0(bus2) + ch3(bus1) — 코퍼스의 fhd_2ch_03 형태.
        케이스 단위로 dual 을 판정하면 0x11/0x12 로 잘못 읽는다.
        """
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": True,
            ".VHL_CAM.i2c1.ch3.enable": True,
            ".VHL_CAM.i2c1.ch3.ae_on": True,
        }}}
        targets = ae_settle_targets(prof)
        assert [(t["label"], t["bus"], t["addr"]) for t in targets] == [
            ("ch0 AE_CTRL", 2, ISP_SINGLE_ADDR),
            ("ch3 AE_CTRL", 1, ISP_SINGLE_ADDR),
        ]

    def test_disabled_channel_does_not_count_toward_bus_dual(self):
        """비활성 채널은 dual 판정에 세지 않는다 — 켠 채널만 ISP 가 붙는다."""
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
            ".VHL_CAM.i2c2.ch0.ae_gain": 512,
            ".VHL_CAM.i2c2.ch1.enable": False,
            ".VHL_CAM.i2c2.ch1.ae_on": True,
        }}}
        assert {t["addr"] for t in ae_settle_targets(prof)} == {ISP_SINGLE_ADDR}

    def test_disabled_channel_is_ignored(self):
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": False,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
            ".VHL_CAM.i2c2.ch0.ae_gain": 512,
        }}}
        assert ae_settle_targets(prof) == []

    def test_channel_without_explicit_ae_on_yields_no_target(self):
        """케이스가 명시하지 않은 값은 보드 잔존값(드리프트) — 단언하지 않는다."""
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.vflip": True,
        }}}
        assert ae_settle_targets(prof) == []

    def test_manual_channel_without_gain_yields_ae_ctrl_only(self):
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
        }}}
        targets = ae_settle_targets(prof)
        assert [t["reg"] for t in targets] == ["0x50 0x02"]

    def test_multiple_channels_are_ordered_deterministically(self):
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c1.ch2.enable": True,
            ".VHL_CAM.i2c1.ch2.ae_on": True,
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
            ".VHL_CAM.i2c2.ch0.ae_gain": 512,
        }}}
        labels = [t["label"] for t in ae_settle_targets(prof)]
        assert labels == ["ch0 AE_CTRL", "ch0 AE_GAIN", "ch2 AE_CTRL"]

    def test_non_int_gain_is_skipped_not_crashing(self):
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
            ".VHL_CAM.i2c2.ch0.ae_gain": "512",
        }}}
        assert [t["reg"] for t in ae_settle_targets(prof)] == ["0x50 0x02"]

    def test_empty_and_malformed_profiles(self):
        assert ae_settle_targets({}) == []
        assert ae_settle_targets(None) == []
        assert ae_settle_targets({"setup": None}) == []
        assert ae_settle_targets({"setup": {"edgeconf_changes": None}}) == []


class TestStageAeSettle:
    """AE 정착 readiness 단계 — 기대값 일치 읽기 2회(간격 >= 3s) + gstApp 경과 하한."""

    _T = [
        {"label": "ch0 AE_CTRL", "bus": 2, "addr": "0x11",
         "reg": "0x50 0x02", "expected": "0x020x90"},
        {"label": "ch0 AE_GAIN", "bus": 2, "addr": "0x11",
         "reg": "0x50 0x06", "expected": "0x020x00"},
    ]

    def _mgr_with_targets(self, out):
        mgr = _mgr()
        mgr._ready_ae_targets = list(self._T)
        mgr.ssh.run.return_value = out
        return mgr

    def test_no_targets_means_stage_passes(self):
        """AE 를 단언하지 않는 케이스는 게이트로 붙잡지 않는다."""
        mgr = _mgr()
        mgr._ready_ae_targets = []
        assert mgr._ready_ae_settle(_clock=_FixedClock()) is True
        mgr.ssh.run.assert_not_called()

    def test_matching_values_need_two_reads_at_least_gap_apart(self):
        mgr = self._mgr_with_targets("e=100\nv=0x020x90\nv=0x020x00")
        clk = _FixedClock(start=1000.0)
        # 1회차 일치 — 아직 정착 아님
        assert mgr._ready_ae_settle(_clock=clk) is False
        # 간격 미달 → 여전히 아님
        clk.v = 1000.0 + AE_SETTLE_MATCH_GAP_SEC - 0.5
        assert mgr._ready_ae_settle(_clock=clk) is False
        # 간격 충족 → 정착
        clk.v = 1000.0 + AE_SETTLE_MATCH_GAP_SEC
        assert mgr._ready_ae_settle(_clock=clk) is True

    def test_transient_value_never_passes(self):
        """전이값(AE_GAIN 0x0100)은 3초 이상 유지돼도 통과하면 안 된다 —
        '안정 2회'가 아니라 '기대값 일치 2회'가 판정 기준인 이유."""
        mgr = self._mgr_with_targets("e=100\nv=0x020x90\nv=0x010x00")
        clk = _FixedClock(start=0.0)
        for t in (0.0, 5.0, 10.0, 30.0):
            clk.v = t
            assert mgr._ready_ae_settle(_clock=clk) is False

    def test_mismatch_resets_match_timer(self):
        mgr = _mgr()
        mgr._ready_ae_targets = list(self._T)
        seq = iter([
            "e=100\nv=0x020x90\nv=0x020x00",   # 일치 → 타이머 시작
            "e=100\nv=0x020x90\nv=0x010x00",   # 전이값으로 후퇴 → 리셋
            "e=100\nv=0x020x90\nv=0x020x00",   # 재일치 → 타이머 재시작
        ])
        mgr.ssh.run.side_effect = lambda *a, **k: next(seq)
        clk = _FixedClock(start=0.0)
        assert mgr._ready_ae_settle(_clock=clk) is False
        assert mgr._ae_match_at == 0.0
        clk.v = 100.0
        assert mgr._ready_ae_settle(_clock=clk) is False
        assert mgr._ae_match_at is None
        assert mgr._ready_ae_settle(_clock=clk) is False
        assert mgr._ae_match_at == 100.0

    def test_gstapp_etime_floor_blocks_even_when_values_match(self):
        """AE 하한 앵커는 boot 가 아니라 gstApp 기동 기준 — 부팅 단축(하드리셋)과 무관."""
        below = AE_SETTLE_GSTAPP_ETIME_SEC - 1
        mgr = self._mgr_with_targets(f"e={below}\nv=0x020x90\nv=0x020x00")
        clk = _FixedClock(start=0.0)
        assert mgr._ready_ae_settle(_clock=clk) is False
        clk.v = 100.0
        assert mgr._ready_ae_settle(_clock=clk) is False
        assert mgr._ae_match_at is None

    def test_gstapp_etime_at_floor_is_accepted(self):
        mgr = self._mgr_with_targets(
            f"e={AE_SETTLE_GSTAPP_ETIME_SEC}\nv=0x020x90\nv=0x020x00")
        clk = _FixedClock(start=0.0)
        assert mgr._ready_ae_settle(_clock=clk) is False   # 1회차
        clk.v = AE_SETTLE_MATCH_GAP_SEC
        assert mgr._ready_ae_settle(_clock=clk) is True

    def test_gstapp_absent_is_not_ready(self):
        """gstApp 미기동이면 'e=' (빈 값) — 보드 실측 출력 형태."""
        mgr = self._mgr_with_targets("e=\nv=0x020x90\nv=0x020x00")
        assert mgr._ready_ae_settle(_clock=_FixedClock()) is False

    def test_missing_value_line_is_not_ready(self):
        """i2c 읽기 실패는 'v=' 빈 줄로 온다(보드 실측) — 줄 수는 유지된다."""
        mgr = self._mgr_with_targets("e=100\nv=0x020x90\nv=")
        assert mgr._ready_ae_settle(_clock=_FixedClock()) is False

    def test_truncated_output_is_not_ready(self):
        """줄 수가 타겟 수와 다르면 정렬을 신뢰할 수 없다 — 통과시키지 않는다."""
        mgr = self._mgr_with_targets("e=100\nv=0x020x90")
        assert mgr._ready_ae_settle(_clock=_FixedClock()) is False

    def test_none_output_is_not_ready(self):
        mgr = self._mgr_with_targets(None)
        assert mgr._ready_ae_settle(_clock=_FixedClock()) is False

    def test_ssh_error_is_not_ready_and_resets(self):
        mgr = _mgr()
        mgr._ready_ae_targets = list(self._T)
        mgr._ae_match_at = 5.0
        mgr.ssh.run.side_effect = RuntimeError("boom")
        assert mgr._ready_ae_settle() is False
        assert mgr._ae_match_at is None

    def test_probe_command_shape(self):
        mgr = self._mgr_with_targets("e=100\nv=0x020x90\nv=0x020x00")
        mgr._ready_ae_settle(_clock=_FixedClock())
        cmd = mgr.ssh.run.call_args[0][0]
        # gstApp 경과초 + 타겟별 i2c 읽기가 한 번의 왕복으로 묶인다.
        assert "ps -o etimes= -C gstApp" in cmd
        assert "i2ctransfer -f -y 2 w2@0x11 0x50 0x02 r2" in cmd
        assert "i2ctransfer -f -y 2 w2@0x11 0x50 0x06 r2" in cmd
        # 센티널 prefix — 읽기 실패로 값이 비어도 줄이 사라지지 않아 정렬이 유지된다.
        assert cmd.count("printf 'v=%s") == 2
        assert "printf 'e=%s" in cmd

    def test_probe_reads_each_channel_at_its_own_address(self):
        """같은 버스 2채널 — 명령이 0x11/0x12 를 각각 써야 한다 (오탐 회귀 방지)."""
        mgr = _mgr()
        mgr._ready_ae_targets = [
            {"label": "ch0 AE_CTRL", "bus": 2, "addr": "0x11",
             "reg": "0x50 0x02", "expected": "0x020x90"},
            {"label": "ch1 AE_CTRL", "bus": 2, "addr": "0x12",
             "reg": "0x50 0x02", "expected": "0x020x99"},
        ]
        mgr.ssh.run.return_value = "e=100\nv=0x020x90\nv=0x020x99"
        assert mgr._ready_ae_settle(_clock=_FixedClock()) is False   # 1회차
        cmd = mgr.ssh.run.call_args[0][0]
        assert "w2@0x11 0x50 0x02" in cmd
        assert "w2@0x12 0x50 0x02" in cmd

    def test_stage_order_between_camera_init_and_recording(self):
        mgr = _mgr()
        mgr._ready_processes_list = ["gstApp"]
        mgr._ready_fsync = True
        mgr._ready_ae_targets = list(self._T)
        mgr._ready_recording_paths = ["/dev/shm", "/mnt/sd_cam"]
        assert [n for n, _ in mgr._stabilize_stages()] == [
            "ssh", "session_anchor", "processes", "camera_init", "ae_settle",
            "recording"]

    def test_stage_absent_when_no_targets(self):
        mgr = _mgr()
        mgr._ready_fsync = True
        mgr._ready_recording_paths = ["/dev/shm"]
        assert [n for n, _ in mgr._stabilize_stages()] == [
            "ssh", "session_anchor", "camera_init", "recording"]

    def test_earlier_stage_timeout_short_circuits_ae_settle(self):
        """앞 단계(camera_init)가 예산을 다 쓰면 ae_settle 은 **평가되지 않는다**.

        wait_until_ready 는 단계 타임아웃에서 즉시 False 를 반환하고, _stabilize 는
        경고 후 진행한다(기존 semantics — monitor 가 최종 검증). 즉 AE 게이트는
        fsync 가 뜨지 않는 상황을 구제하지 않는다: 그 경우 카메라 자체가 init 되지
        않은 것이라 케이스는 어차피 실패한다. 예산 공유 구조를 명시적으로 고정한다.
        """
        mgr = _mgr()
        evaluated = []
        stages = [
            ("camera_init", lambda: (evaluated.append("camera_init"), False)[1]),
            ("ae_settle", lambda: (evaluated.append("ae_settle"), True)[1]),
        ]
        ok = mgr.wait_until_ready(
            stages, poll_interval=10, debounce=2, timeout=30,
            _sleep=lambda s: None, _clock=_Clock(step=10.0),
        )
        assert ok is False
        assert "ae_settle" not in evaluated

    def test_run_setup_stores_targets_and_resets_timer(self):
        mgr = _mgr()
        mgr._ae_match_at = 42.0
        mgr.run_setup({}, ready_ae_targets=list(self._T))
        assert mgr._ready_ae_targets == self._T
        assert mgr._ae_match_at is None


class TestReadinessKwargsAeSettle:
    def test_camera_profile_yields_targets(self):
        prof = {"setup": {"edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
            ".VHL_CAM.i2c2.ch0.ae_gain": 512,
        }}}
        kw = readiness_kwargs(prof)
        assert [t["label"] for t in kw["ready_ae_targets"]] == [
            "ch0 AE_CTRL", "ch0 AE_GAIN"]

    def test_camera_init_opt_out_also_disables_ae_settle(self):
        """camera_init_required: false 는 카메라 게이트 전체 opt-out 이다."""
        prof = {"setup": {"camera_init_required": False, "edgeconf_changes": {
            ".VHL_CAM.i2c2.ch0.enable": True,
            ".VHL_CAM.i2c2.ch0.ae_on": False,
        }}}
        assert readiness_kwargs(prof)["ready_ae_targets"] == []

    def test_non_camera_profile_yields_no_targets(self):
        kw = readiness_kwargs({"setup": {"edgeconf_changes": {".NETWORK.wifi.ssid": "x"}}})
        assert kw["ready_ae_targets"] == []


class TestAeSettleAddressMatchesCaseCorpus:
    """AE 정착이 읽는 주소가 케이스 자신의 readback 주소와 일치해야 한다.

    케이스 custom_commands 의 `i2ctransfer ... w2@ADDR` 는 보드에서 검증된 정본이다.
    readiness 게이트가 다른 주소를 읽으면 (a) dual 에서 이웃 채널 값으로 오탐 통과,
    (b) single 에서 무응답으로 게이트 미개방 이 된다. 코퍼스 전체를 대조해 두면
    새 케이스가 다른 규칙으로 들어와도 여기서 잡힌다.
    """

    @staticmethod
    def _case_addrs(path):
        """케이스 yaml 에서 (ch, bus) → readback 주소 집합을 뽑는다."""
        name_re = re.compile(r"-\s*name:\s*ch(\d)\s+(?:AE_CTRL|AE_GAIN|ROTATION|AWB_CTRL)")
        cmd_re = re.compile(r"command:\s*i2ctransfer -f -y (\d) w2@(0x[0-9a-f]{2})")
        lines = path.read_text().splitlines()
        found = {}
        for i, line in enumerate(lines):
            nm = name_re.search(line)
            if not nm:
                continue
            for nxt in lines[i + 1:i + 3]:
                cm = cmd_re.search(nxt)
                if cm:
                    found.setdefault((int(nm.group(1)), int(cm.group(1))), set()).add(
                        cm.group(2))
                    break
        return found

    def test_every_case_readback_address_matches_derived_target(self):
        import pathlib

        import yaml

        root = pathlib.Path(__file__).resolve().parent.parent / "profiles"
        checked = 0
        mismatches = []
        for path in sorted(root.rglob("*.yaml")):
            try:
                prof = yaml.safe_load(path.read_text()) or {}
            except Exception:
                continue
            if not isinstance(prof, dict):
                continue
            targets = ae_settle_targets(prof)
            if not targets:
                continue
            case_addrs = self._case_addrs(path)
            for t in targets:
                ch = int(t["label"].split()[0][2:])
                expected_addrs = case_addrs.get((ch, t["bus"]))
                if not expected_addrs:
                    continue  # 케이스가 그 채널 레지스터를 읽지 않으면 대조 대상 아님
                checked += 1
                if t["addr"] not in expected_addrs:
                    mismatches.append(
                        f"{path.name} ch{ch} bus{t['bus']}: "
                        f"derived {t['addr']} vs case {sorted(expected_addrs)}")
        # 코퍼스가 비면 이 테스트가 조용히 무의미해진다 — 대조 건수를 하한으로 고정.
        # 2026-08-21 기준 실측 96건(케이스가 ae_on 을 명시한 채널만 대조 대상).
        assert checked >= 80, f"대조 건수가 너무 적다 ({checked}) — 코퍼스 탐색 경로 확인"
        assert not mismatches, "\n".join(mismatches[:10])


class TestSessionAnchor:
    """세션 앵커 — 케이스 체크가 "이 시각 이후" 로그만 보게 하는 기준점.

    지금까지 각 케이스가 `uptime -s`(부팅 시각)를 직접 읽었는데, 그건 "케이스 사이의
    재시작 = 재부팅"이라는 전제에 의존한다. 하드리셋으로 재부팅을 대체하면
    `uptime -s` 가 안 바뀌어 직전 케이스 세션이 매칭된다. 앵커를 파일로 외부화해
    바꿀 곳을 `_write_session_anchor` 한 곳으로 모은다.
    """

    def test_writes_boot_time_and_boot_id(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "2026-08-21 14:42:05\n80c4ba4d-8ab6-4a3a-a663-c06d"
        assert mgr._write_session_anchor() is True
        cmd = mgr.ssh.run.call_args[0][0]
        assert "uptime -s" in cmd                       # 1행 = 앵커 시각
        assert "/proc/sys/kernel/random/boot_id" in cmd  # 2행 = 잔존 판별용
        assert SESSION_ANCHOR_PATH in cmd

    def test_always_rewrites_rather_than_skipping_on_match(self):
        """세션 시작마다 무조건 다시 쓴다 — 조건부로 만들면 하드리셋에서 깨진다.

        "기존 값이 유효하면 건너뛴다"로 구현하면 같은 부팅 안에서 새 세션이 시작되는
        하드리셋일 때 이전 세션의 앵커가 그대로 남는다 — 정확히 이 게이트가 막으려던
        상황이다. 오늘은 앵커 = 부팅 시각이라 값이 불변이라 디바운스 재호출에서도
        내용이 같다(멱등).
        """
        mgr = _mgr()
        mgr.ssh.run.return_value = "2026-08-21 14:42:05\n2026-08-21 14:42:05"
        mgr._write_session_anchor()
        cmd = mgr.ssh.run.call_args[0][0]
        # 기존 파일을 읽어 비교하는 분기가 없어야 한다.
        assert "NR==2" not in cmd
        assert f"> {SESSION_ANCHOR_PATH}" in cmd

    def test_returns_false_on_missing_or_short_output(self):
        mgr = _mgr()
        for out in (None, "", "2026-08-21 14:42:05", "\n\n"):
            mgr.ssh.run.return_value = out
            assert mgr._write_session_anchor() is False, out

    def test_returns_false_on_ssh_error(self):
        mgr = _mgr()
        mgr.ssh.run.side_effect = RuntimeError("boom")
        assert mgr._write_session_anchor() is False

    def test_stage_present_only_for_camera_cases(self):
        mgr = _mgr()
        mgr._ready_recording_paths = ["/dev/shm"]
        # 비카메라: 앵커를 읽는 custom_commands 가 없으므로 단계도 없다.
        assert "session_anchor" not in [n for n, _ in mgr._stabilize_stages()]
        mgr._ready_fsync = True
        stages = [n for n, _ in mgr._stabilize_stages()]
        assert stages[:2] == ["ssh", "session_anchor"]

    def test_run_setup_resets_dmesg_anchor(self):
        mgr = _mgr()
        mgr._dmesg_anchor_uptime = 461.7
        mgr.run_setup({})
        # 재부팅 경로 기본값 — 링버퍼가 비워지므로 0 이면 충분하다.
        assert mgr._dmesg_anchor_uptime == 0.0


class TestCasesUseSessionAnchor:
    """케이스가 부팅 앵커를 직접 읽지 않고 세션 앵커를 거치는지 상시 고정.

    새 케이스가 `BOOT=$(uptime -s)` 관행으로 다시 들어오면 하드리셋 전환 때 조용히
    직전 케이스 영상으로 검증하게 된다 — 그 재발을 여기서 잡는다.
    """

    def test_no_case_reads_uptime_boot_time_directly(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "profiles"
        offenders = []
        anchored = 0
        for path in sorted(root.rglob("*.yaml")):
            text = path.read_text()
            if "uptime -s" not in text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "uptime -s" not in line:
                    continue
                if SESSION_ANCHOR_PATH in line:
                    anchored += line.count("uptime -s")
                else:
                    offenders.append(f"{path.name}:{i}")
        assert not offenders, (
            "세션 앵커를 거치지 않고 uptime -s 를 직접 읽는 곳:\n"
            + "\n".join(offenders[:10]))
        # 코퍼스가 비면 테스트가 조용히 무의미해진다 — 2026-08 기준 실측 38건.
        assert anchored >= 30, f"앵커 사용처가 너무 적다 ({anchored})"

    def test_anchor_snippet_falls_back_when_file_absent(self):
        """리더 스니펫은 파일이 없거나 stale 이면 `uptime -s` 로 폴백해야 한다.

        /tmp 가 tmpfs 가 아니라 재부팅에도 파일이 남으므로, 2행(기록 시점의 부팅
        시각) 대조가 stale 판정의 핵심이다. 보드 실측으로 4개 시나리오 확인함.
        """
        import pathlib

        import yaml

        root = pathlib.Path(__file__).resolve().parent.parent / "profiles"
        checked = 0
        for path in sorted(root.rglob("*.yaml")):
            prof = yaml.safe_load(path.read_text())
            if not isinstance(prof, dict):
                continue
            for cmd in ((prof.get("checks") or {}).get("custom_commands") or []):
                command = cmd.get("command", "")
                if SESSION_ANCHOR_PATH not in command:
                    continue
                checked += 1
                # 2행 = boot_id 대조(잔존 거부) + 빈 값일 때 uptime -s 폴백.
                # boot_id 인 이유: uptime -s 는 같은 부팅에서도 ±1초 흔들려
                # (보드 실측) 문자열 대조에 쓰면 jitter 마다 앵커가 무시된다.
                assert "/proc/sys/kernel/random/boot_id" in command, cmd.get("name")
                assert "NR==2 && $0==i" in command, cmd.get("name")
                assert '[ -z "$BOOT" ] && BOOT=$(uptime -s)' in command, cmd.get("name")
        assert checked >= 30, f"앵커 사용처가 너무 적다 ({checked})"
