"""tests/test_setup_camera_ready.py — 카메라 init readiness 프로브의 셸 계약 (#85 C).

fsync(kern.log) 게이트의 대체 — 생성 명령을 **실제 셸에서 돌려** exit 0 + 출력
계약과, 그 출력이 게이트 판정으로 이어지는 파이프 전체를 확인한다.
(판정 로직 단위는 tests/test_stabilize_ready.py::TestStageCameraInitCamState.)

cam_state 의 /tmp 는 부팅마다 비워진다(systemd-tmpfiles — tmpfs 여부와 무관,
2026-08-22 실측) — kern.log 시절의 부팅 경계·앵커
델타·폴백 경고 기제가 통째로 불필요해진 근거다. 검증 축은 state=healthy(동작
확인)와 heartbeat 신선도(감시자 생존 — 죽은 채 남은 healthy 차단) 둘이다.
"""
from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock

from setup import CAM_READY_SETTLE_SEC, CAM_STATE_DIR, SetupManager


def _mgr():
    return SetupManager(MagicMock(), reboot_timeout=300, poll_interval=10)


class _Clock:
    def __init__(self, start=0.0):
        self.v = start

    def __call__(self):
        return self.v


def _run_probe(tmp_path, state=None, timestamp=None):
    """프로브 명령을 로컬 셸에서 실행해 exit 0 + 출력 계약을 단언하고 출력을 반환."""
    mgr = _mgr()
    cmd = mgr._cam_state_ready_probe_command().replace(CAM_STATE_DIR, str(tmp_path))
    if state is not None:
        (tmp_path / "state").write_text(state)
    if timestamp is not None:
        (tmp_path / "timestamp").write_text(timestamp)
    r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True)
    assert r.returncode == 0, (
        f"exit {r.returncode} — ssh.run 이 None 을 받는다: {r.stderr.strip()[:80]}")
    assert r.stdout.strip(), "무출력 — ssh.run strip 후 빈 문자열"
    return r.stdout.strip()


def _verdict_after_settle(out):
    """셸 출력 그대로를 게이트에 먹여 (최초 관측, settle 후) verdict 쌍을 반환."""
    mgr = _mgr()
    mgr.ssh.run.return_value = out
    clk = _Clock(0.0)
    first = mgr._ready_cam_state(_clock=clk)
    clk.v = CAM_READY_SETTLE_SEC
    return first, mgr._ready_cam_state(_clock=clk)


class TestProbeShellContract:
    def test_healthy_fresh_opens_after_settle(self, tmp_path):
        out = _run_probe(tmp_path, state="healthy\n",
                         timestamp=str(int(time.time())))
        first, settled = _verdict_after_settle(out)
        assert first is False and settled is True, out

    def test_absent_dir_reports_and_blocks(self, tmp_path):
        out = _run_probe(tmp_path)              # state 도 timestamp 도 없음
        assert out.startswith(";N;;"), out
        assert _verdict_after_settle(out) == (False, False)

    def test_not_healthy_blocks(self, tmp_path):
        out = _run_probe(tmp_path, state="recovering",
                         timestamp=str(int(time.time())))
        assert out.startswith("recovering;"), out
        assert _verdict_after_settle(out) == (False, False)

    def test_stale_timestamp_blocks(self, tmp_path):
        """healthy 가 남아 있어도 감시자가 죽었으면 게이트가 열리면 안 된다."""
        out = _run_probe(tmp_path, state="healthy",
                         timestamp=str(int(time.time()) - 300))
        assert _verdict_after_settle(out) == (False, False)

    def test_zero_timestamp_blocks(self, tmp_path):
        """0 은 cam_state_init 초기값 — BG_Check 가 한 번도 touch 안 한 상태."""
        out = _run_probe(tmp_path, state="healthy", timestamp="0")
        assert _verdict_after_settle(out) == (False, False)

    def test_garbage_timestamp_blocks(self, tmp_path):
        out = _run_probe(tmp_path, state="healthy", timestamp="not-a-number")
        assert _verdict_after_settle(out) == (False, False)

    def test_whitespace_is_trimmed(self, tmp_path):
        """state/timestamp 의 공백·개행은 프로브의 tr 이 걷어낸다."""
        out = _run_probe(tmp_path, state=" healthy \n",
                         timestamp=f"  {int(time.time())}\n")
        first, settled = _verdict_after_settle(out)
        assert settled is True, out
