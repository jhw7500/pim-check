"""
tests/test_checks_frame_flow.py - FrameFlowCheck 단위 테스트

fixture 는 2026-10-19 보드의 /run/pim-camera/gstApp.json 실측(ch0 만 enabled,
fps 15, 3.0초 뒤 enc_queue_input 33058→33103 = 45 프레임)을 따른다.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from checks import checks_for_scope
from checks.frame_flow import FrameFlowCheck
from verify_retry import is_stabilization_reason

BOOT_ID = "b4c558c4-ed52-436f-af21-bdd8bba50cea"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 보드 실측 원문 (ch1~ch3 는 DISABLED).
REAL_FIRST = (
    '{"schema":1,"producer":"gstApp","boot_id":"b4c558c4-ed52-436f-af21-bdd8bba50cea",'
    '"pid":1005,"sequence":2206,"observed_monotonic_ms":2239001,"observations":['
    '{"block":"gstreamer","scope":{"kind":"channel","id":"ch0","channels":[0]},"status":"OK",'
    '"code":"NONE","count":0,"evidence":[{"name":"enc_queue_input","source":"gstApp","value":33058}]},'
    '{"block":"recording","scope":{"kind":"channel","id":"ch0","channels":[0]},"status":"OK",'
    '"code":"NONE","count":0,"evidence":[{"name":"seconds_since_fragment","source":"gstApp","value":22}]},'
    '{"block":"gstreamer","scope":{"kind":"channel","id":"ch1","channels":[1]},"status":"N/A",'
    '"code":"DISABLED","count":0,"evidence":[{"name":"configured","source":"gstApp","value":false}]},'
    '{"block":"recording","scope":{"kind":"channel","id":"ch1","channels":[1]},"status":"N/A",'
    '"code":"DISABLED","count":0,"evidence":[{"name":"configured","source":"gstApp","value":false}]},'
    '{"block":"gstreamer","scope":{"kind":"channel","id":"ch2","channels":[2]},"status":"N/A",'
    '"code":"DISABLED","count":0,"evidence":[{"name":"configured","source":"gstApp","value":false}]},'
    '{"block":"recording","scope":{"kind":"channel","id":"ch2","channels":[2]},"status":"N/A",'
    '"code":"DISABLED","count":0,"evidence":[{"name":"configured","source":"gstApp","value":false}]},'
    '{"block":"gstreamer","scope":{"kind":"channel","id":"ch3","channels":[3]},"status":"N/A",'
    '"code":"DISABLED","count":0,"evidence":[{"name":"configured","source":"gstApp","value":false}]},'
    '{"block":"recording","scope":{"kind":"channel","id":"ch3","channels":[3]},"status":"N/A",'
    '"code":"DISABLED","count":0,"evidence":[{"name":"configured","source":"gstApp","value":false}]}'
    '],"status":"OK","producer_data":{"stall_sec":3,"no_growth_sec":180}}'
)
REAL_SECOND = (REAL_FIRST
               .replace('"sequence":2206', '"sequence":2209')
               .replace('"observed_monotonic_ms":2239001', '"observed_monotonic_ms":2242001')
               .replace('"value":33058', '"value":33103'))
REAL_EDGECONF = "[15,true,false,false,false]"

CONFIG = {"frame_flow": {"enabled": True, "path": "/run/pim-camera/gstApp.json",
                         "sample_sec": 3, "fps_tolerance_pct": 10}}


def _snap(counters, *, pid=1005, boot_id=BOOT_ID, seq=2206, t_ms=2239001, statuses=None):
    """counters: {chN: int(OK, enc_queue_input) | None(DISABLED)}; statuses 로 status/code 덮어쓰기."""
    statuses = statuses or {}
    obs = []
    for n in range(4):
        ch = f"ch{n}"
        value = counters.get(ch)
        if value is None:
            status, code = "N/A", "DISABLED"
            evidence = [{"name": "configured", "source": "gstApp", "value": False}]
        else:
            status, code = "OK", "NONE"
            evidence = [{"name": "enc_queue_input", "source": "gstApp", "value": value}]
        status, code = statuses.get(ch, (status, code))
        scope = {"kind": "channel", "id": ch, "channels": [n]}
        obs.append({"block": "gstreamer", "scope": scope, "status": status, "code": code,
                    "count": 0, "evidence": evidence})
        obs.append({"block": "recording", "scope": scope, "status": status, "code": code,
                    "count": 0, "evidence": []})
    return json.dumps({"schema": 1, "producer": "gstApp", "boot_id": boot_id, "pid": pid,
                       "sequence": seq, "observed_monotonic_ms": t_ms,
                       "observations": obs, "status": "OK"})


def _output(first, second, edgeconf=REAL_EDGECONF):
    return f"A={first}\nB={second}\nE={edgeconf}"


def _validate(output, config=CONFIG):
    return FrameFlowCheck().validate({"output": output}, config)


class TestFrameFlowCollect(unittest.TestCase):
    def test_disabled_by_default_makes_no_ssh_call(self):
        ssh = MagicMock()
        data = FrameFlowCheck().collect(ssh, {})
        self.assertTrue(data.get("skipped"))
        ssh.run.assert_not_called()
        self.assertEqual(FrameFlowCheck().validate(data, {})[0], True)

    def test_case_opt_out_with_null_path_skips(self):
        ssh = MagicMock()
        cfg = {"frame_flow": {"enabled": True, "path": None}}
        data = FrameFlowCheck().collect(ssh, cfg)
        self.assertTrue(data.get("skipped"))
        ssh.run.assert_not_called()

    def test_single_ssh_command_reads_twice_and_edgeconf(self):
        ssh = MagicMock()
        ssh.run.return_value = _output(REAL_FIRST, REAL_SECOND)
        data = FrameFlowCheck().collect(ssh, CONFIG)
        self.assertEqual(ssh.run.call_count, 1)
        cmd = ssh.run.call_args[0][0]
        self.assertEqual(cmd.count("/run/pim-camera/gstApp.json"), 2)
        self.assertIn("sleep 3", cmd)
        self.assertIn("/root/shared_v/edgeconf_pim.json", cmd)
        for key in (".VHL_CAM.fps", ".VHL_CAM.i2c2.ch0.enable", ".VHL_CAM.i2c2.ch1.enable",
                    ".VHL_CAM.i2c1.ch2.enable", ".VHL_CAM.i2c1.ch3.enable"):
            self.assertIn(key, cmd)
        self.assertEqual(data["output"], _output(REAL_FIRST, REAL_SECOND))


class TestFrameFlowValidatePass(unittest.TestCase):
    def test_real_board_sample_passes(self):
        passed, reason = _validate(_output(REAL_FIRST, REAL_SECOND))
        self.assertTrue(passed, reason)
        self.assertIn("ch0 15.0fps", reason)

    def test_all_four_channels_within_tolerance(self):
        first = _snap({"ch0": 100, "ch1": 200, "ch2": 300, "ch3": 400})
        # 3.0s 동안 각 88~92 프레임 = 29.3~30.7fps (기대 30 ±10%)
        second = _snap({"ch0": 190, "ch1": 288, "ch2": 392, "ch3": 490}, seq=2209, t_ms=2242001)
        passed, reason = _validate(_output(first, second, "[30,true,true,true,true]"))
        self.assertTrue(passed, reason)

    def test_tolerance_boundary(self):
        first = _snap({"ch0": 0})
        inside = _snap({"ch0": 49}, seq=2209, t_ms=2242001)    # 16.33fps (≤16.5)
        outside = _snap({"ch0": 50}, seq=2209, t_ms=2242001)   # 16.67fps (>16.5)
        self.assertTrue(_validate(_output(first, inside))[0])
        self.assertFalse(_validate(_output(first, outside))[0])


class TestFrameFlowHardFail(unittest.TestCase):
    """확정 결함 — stabilization 토큰이 없어야 stable-fail 조기 종료가 동작한다."""

    def _assert_hard(self, output, *fragments):
        passed, reason = _validate(output)
        self.assertFalse(passed)
        self.assertFalse(is_stabilization_reason(reason), reason)
        for frag in fragments:
            self.assertIn(frag, reason)
        return reason

    def test_fps_out_of_range_names_channel_and_measured_vs_expected(self):
        first = _snap({"ch0": 33058})
        second = _snap({"ch0": 33088}, seq=2209, t_ms=2242001)   # 30 frames / 3s = 10fps
        self._assert_hard(_output(first, second), "ch0", "10.0", "15")

    def test_enabled_channel_reported_disabled(self):
        first = _snap({"ch0": 33058})       # ch1 DISABLED
        second = _snap({"ch0": 33103}, seq=2209, t_ms=2242001)
        self._assert_hard(_output(first, second, "[15,true,true,false,false]"), "ch1", "DISABLED")

    def test_disabled_channel_counting_frames(self):
        first = _snap({"ch0": 33058, "ch2": 1000})
        second = _snap({"ch0": 33103, "ch2": 1045}, seq=2209, t_ms=2242001)
        self._assert_hard(_output(first, second), "ch2", "disabled", "counting frames", "15.0fps")

    def test_disabled_channel_not_reporting_disabled(self):
        first = _snap({"ch0": 33058, "ch3": 7})
        second = _snap({"ch0": 33103, "ch3": 7}, seq=2209, t_ms=2242001)
        self._assert_hard(_output(first, second), "ch3", "DISABLED")

    def test_pid_change_between_reads(self):
        second = _snap({"ch0": 33103}, pid=2001, seq=2209, t_ms=2242001)
        self._assert_hard(_output(_snap({"ch0": 33058}), second), "pid", "1005", "2001")

    def test_boot_id_change_between_reads(self):
        second = _snap({"ch0": 33103}, boot_id="other-boot", seq=2209, t_ms=2242001)
        self._assert_hard(_output(_snap({"ch0": 33058}), second), "boot_id")

    def test_edgeconf_unreadable(self):
        self._assert_hard(_output(REAL_FIRST, REAL_SECOND, ""), "edgeconf")

    def test_hard_issue_is_not_masked_by_a_transient_one(self):
        """ch0 정지(transient) + ch1 fps 이탈(hard) — reason 전체가 stabilization 으로 읽히면
        monitor 가 확정 결함을 '준비 중'으로 오인해 끝까지 기다린다."""
        first = _snap({"ch0": 500, "ch1": 100})
        second = _snap({"ch0": 500, "ch1": 115}, seq=2209, t_ms=2242001)   # ch1 5fps
        self._assert_hard(_output(first, second, "[15,true,true,false,false]"), "ch1")


class TestFrameFlowTransient(unittest.TestCase):
    """부팅 직후 과도 상태 — stabilization 토큰으로 monitor 가 계속 기다리게 한다."""

    def _assert_transient(self, output, *fragments):
        passed, reason = _validate(output)
        self.assertFalse(passed)
        self.assertTrue(is_stabilization_reason(reason), reason)
        for frag in fragments:
            self.assertIn(frag, reason)
        return reason

    def test_snapshot_file_absent(self):
        self._assert_transient(_output("", "", REAL_EDGECONF), "gstApp.json")

    def test_malformed_json(self):
        self._assert_transient(_output('{"schema":1,', REAL_SECOND))

    def test_no_output(self):
        self._assert_transient(None)

    def test_sequence_not_advancing(self):
        self._assert_transient(_output(REAL_FIRST, REAL_FIRST), "sequence")

    def test_enabled_counter_not_advancing_names_channel_and_fps(self):
        first = _snap({"ch0": 5})
        second = _snap({"ch0": 5}, seq=2209, t_ms=2242001)
        self._assert_transient(_output(first, second), "ch0", "0.0", "15")


class TestFrameFlowMonitorVerdict(unittest.TestCase):
    """until_pass monitor + verify retry 를 통과한 최종 판정."""

    def _engine(self, output, duration=50, interval=5):
        from engine import Engine
        ssh = MagicMock()
        ssh.run.return_value = output
        ssh.check_connectivity.return_value = True
        profile = {"monitor": {"duration_sec": duration, "interval_sec": interval},
                   "checks": CONFIG, "retry_policy": {}}
        engine = Engine(ssh, profile)
        engine.checks = [FrameFlowCheck()]
        return engine, ssh, profile

    @patch("verify_retry.time.sleep")
    @patch("engine.time.sleep")
    def test_stalled_counter_waits_whole_window_and_still_fails(self, _es, _vs):
        """pim-package#191 (SIGKILL 뒤 카운터 5 에 고착) — 과도 상태로 분류돼 조기 종료는
        안 하지만, 창이 끝나면 최종 판정은 FAIL 이어야 한다."""
        from plan import _run_single_case
        stalled = _output(_snap({"ch0": 5}), _snap({"ch0": 5}, seq=2209, t_ms=2242001))
        engine, ssh, profile = self._engine(stalled)
        results, collected, total = engine.run_monitor(until_pass=True)
        self.assertEqual(collected, total)            # 전 구간 샘플링 (조기 종료 없음)
        self.assertFalse(results[0]["passed"])

        results, passed, error = _run_single_case(
            ssh, profile, "c", engine_factory=lambda s, p: engine, setup_factory=None,
            monitor_until_pass=True)
        self.assertIsNone(error)
        self.assertFalse(passed)
        self.assertIn("ch0", results[0]["reason"])

    @patch("engine.time.sleep")
    def test_definitive_fps_fail_exits_early(self, _es):
        from engine import STABLE_FAIL_SAMPLES
        bad = _output(_snap({"ch0": 0}), _snap({"ch0": 30}, seq=2209, t_ms=2242001))
        engine, _ssh, _p = self._engine(bad)
        results, collected, total = engine.run_monitor(until_pass=True)
        self.assertEqual(collected, STABLE_FAIL_SAMPLES)
        self.assertLess(collected, total)
        self.assertFalse(results[0]["passed"])


class TestFrameFlowRegistry(unittest.TestCase):
    def test_registered_as_snapshot_check(self):
        self.assertIn("frame_flow", {c.name for c in checks_for_scope("snapshot")})

    def test_base_profile_keeps_it_off(self):
        base = yaml.safe_load((PROJECT_ROOT / "profiles" / "base.yaml").read_text())
        cfg = base["checks"]["frame_flow"]
        self.assertIs(cfg["enabled"], False)
        self.assertEqual(cfg["path"], "/run/pim-camera/gstApp.json")


if __name__ == "__main__":
    unittest.main()
