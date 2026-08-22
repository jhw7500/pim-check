"""tests/test_fsync_fallback_warning.py — fsync 앵커 폴백은 조용히 발동하면 안 된다 (pim-check#69 (d)).

`_ready_dmesg_fsync` 는 앵커 이후(`n`)로 판정하되, 타임스탬프를 하나도 못 읽으면
(`p == 0`) 총건수(`t`)로 폴백한다 — 게이트가 영영 안 열리는 것보다 낫기 때문이다.

문제는 이 폴백이 **아무 신호 없이** 발동한다는 것이다. 폴백이 걸리면 #66 의 앵커
델타가 통째로 무효화되고 "존재만으로 판정" 하던 이전 동작으로 되돌아가는데, 로그는
정상 경로와 똑같이 보인다. 소스 포맷이 바뀌었을 때(예: `/dev/kmsg` 처럼 줄머리에
대괄호가 없는 소스) 정확히 이 조합이 나온다.

#67 의 원칙과 같다 — **이상 징후가 정상 경로와 같은 모양을 입으면 안 된다.**

경고는 **1회만** 낸다. readiness 는 폴링이라 매 회 찍으면 로그를 덮는다.
"""
from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import MagicMock

from setup import SetupManager


def _mgr(probe_output: str):
    mgr = SetupManager(MagicMock(), reboot_timeout=300, poll_interval=10)
    mgr._local0_log = MagicMock()
    mgr.ssh.run.return_value = probe_output
    return mgr


def _probe(mgr, times: int = 1) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for _ in range(times):
            mgr._ready_dmesg_fsync()
    return buf.getvalue()


class TestFsyncAnchorFallbackWarns(unittest.TestCase):
    def test_unparsable_timestamps_are_reported(self):
        """t>0 인데 p==0 — 소스는 뭔가 주는데 파서가 못 읽는다. 폴백 중임을 알려야 한다."""
        out = _probe(_mgr("t=12 p=0 n=0"))
        self.assertIn("WARNING", out)
        self.assertIn("p=0", out)

    def test_warning_is_emitted_once_per_instance(self):
        """readiness 는 폴링이다 — 매 회 찍으면 로그를 덮는다."""
        out = _probe(_mgr("t=12 p=0 n=0"), times=5)
        self.assertEqual(out.count("WARNING"), 1)

    def test_no_warning_when_timestamps_parse(self):
        out = _probe(_mgr("t=12 p=12 n=3"))
        self.assertEqual(out, "")

    def test_no_warning_when_source_is_simply_empty(self):
        """로그가 아직 안 뜬 것은 폴백이 아니라 정상 대기 상태다."""
        out = _probe(_mgr("t=0 p=0 n=0"))
        self.assertEqual(out, "")

    def test_fallback_still_works(self):
        """경고를 붙이면서 폴백 동작 자체를 깨뜨리면 안 된다 — 게이트는 열려야 한다."""
        mgr = _mgr("t=12 p=0 n=0")
        clock = iter([0.0, 999.0])
        with contextlib.redirect_stdout(io.StringIO()):
            first = mgr._ready_dmesg_fsync(_clock=lambda: next(clock))
            second = mgr._ready_dmesg_fsync(_clock=lambda: next(clock))
        self.assertFalse(first, "최초 관측 직후에는 settle 대기여야 한다")
        self.assertTrue(second, "settle 시간이 지나면 폴백 경로로도 게이트가 열려야 한다")


if __name__ == "__main__":
    unittest.main()
