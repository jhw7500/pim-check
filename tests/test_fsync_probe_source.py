"""tests/test_fsync_probe_source.py — fsync readiness probe 의 소스와 부팅 경계 (pim-check#69 (b′)).

readiness 게이트(`camera_init`)는 `dmesg` 를 읽었다. 그런데 dmesg 는 두 기제로 비는데
(`SYSLOG_ACTION_CLEAR`, IMU 폭주로 인한 wrap) **게이트가 통과한 직후 체크가
`FAIL:NO_DMESG` 로 떨어지는** 조합이 실제로 관측됐다. 소스를 `kern.log` 로 옮긴다.

⚠ 옮기면서 **새 위험이 생긴다.** dmesg 는 부팅마다 비워져 "앵커 0 = 이번 부팅"이
공짜로 성립했지만, kern.log 는 재부팅을 넘어 살아남는다(4월치까지 `.gz` 보존).
그대로 옮기면 **과거 부팅의 fsync 마커까지 세어 게이트가 즉시 열린다** — 카메라가
아직 초기화되지 않았는데 ready 로 판정하는 거짓 통과다.

그래서 부팅 경계를 **monotonic 이 감소하는 지점**으로 잡아 마지막 부팅 구간만 센다.
이 파일은 그 성질을 **awk 를 실제로 돌려서** 확인한다 — 명령 문자열을 grep 하는
형태 검사로는 "정규식이 있다" 까지만 알 수 있고 "제대로 센다" 는 알 수 없다.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from setup import KERN_LOG_PATH, SetupManager

# kern.log 실제 포맷 — monotonic 이 줄머리가 아니라 `kernel[notice][   N.NNN]` 안에 있다.
def _line(ts: float, when: str = "2026-08-22 02:08:47", marker: bool = True) -> str:
    body = ("[I2C:1][max9296.c:4612] max9296_fsync side fps : 30, low : 32333"
            if marker else "[I2C:1][max9296.c:1001] some other kernel message")
    return f"{when}.219 kernel[notice][{ts:11.6f}] {body}"


class TestFsyncProbeRunsAgainstKernLog(unittest.TestCase):
    def _run(self, lines: list[str], anchor: float) -> dict:
        """probe 명령을 실제 셸에서 돌리고 t/p/n 을 돌려준다."""
        mgr = SetupManager(MagicMock())
        mgr._dmesg_anchor_uptime = anchor
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "kern.log"
            log.write_text("\n".join(lines) + "\n")
            cmd = mgr._dmesg_fsync_probe_command().replace(KERN_LOG_PATH, str(log))
            out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, f"probe 가 exit≠0: {out.stderr}")
        parsed = {}
        for token in out.stdout.split():
            k, _, v = token.partition("=")
            parsed[k] = int(v)
        return parsed

    def test_counts_markers_after_anchor_in_current_boot(self):
        got = self._run([_line(20.0), _line(25.5), _line(30.0)], anchor=24.0)
        self.assertEqual(got, {"t": 3, "p": 3, "n": 2})

    def test_previous_boot_markers_are_not_counted(self):
        """kern.log 는 재부팅을 넘어 산다 — 이전 부팅 구간이 섞이면 게이트가 조기 개방된다."""
        lines = [
            _line(100.0, "2026-08-21 09:00:00"),   # 이전 부팅
            _line(200.0, "2026-08-21 09:02:00"),   # 이전 부팅
            _line(25.5, "2026-08-22 02:08:47"),    # ← monotonic 감소 = 부팅 경계
            _line(30.0, "2026-08-22 02:08:52"),
        ]
        got = self._run(lines, anchor=0.0)
        self.assertEqual(got["n"], 2, "이전 부팅 마커까지 셌다 — 게이트가 조기 개방된다")

    def test_anchor_still_gates_within_the_current_boot(self):
        """하드리셋(SoC 재부팅 없음)에서는 같은 부팅 안에서 앵커가 유일한 경계다."""
        got = self._run([_line(10.0), _line(20.0), _line(30.0)], anchor=25.0)
        self.assertEqual(got["n"], 1)

    def test_empty_or_missing_source_still_reports(self):
        """소스가 없어도 exit 0 + 출력 — `ssh.run` 은 exit≠0 에 None 을 준다."""
        mgr = SetupManager(MagicMock())
        cmd = mgr._dmesg_fsync_probe_command().replace(KERN_LOG_PATH, "/nonexistent/kern.log")
        out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("t=0", out.stdout)

    def test_non_marker_lines_are_ignored(self):
        got = self._run([_line(25.5, marker=False), _line(26.0)], anchor=0.0)
        self.assertEqual(got["t"], 1)

    def test_probe_does_not_read_the_ring_buffer(self):
        mgr = SetupManager(MagicMock())
        cmd = mgr._dmesg_fsync_probe_command()
        self.assertNotIn("dmesg", cmd)
        self.assertIn(KERN_LOG_PATH, cmd)


if __name__ == "__main__":
    unittest.main()
