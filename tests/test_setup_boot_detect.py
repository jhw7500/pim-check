"""
tests/test_setup_boot_detect.py — reboot_and_wait 의 부팅 판정 (boot_id 기반).

녹화 중 reboot 을 보내면 프로세스 종료가 길어져 실제 재부팅이 한참 뒤에 일어날 수
있다. 그동안 **이전 부팅**이 SSH 에 응답하므로 "응답한다 = 복귀했다"로 판정하면
이전 부팅 위에서 readiness 를 돌린다. 판정은 reboot 직전의 boot_id 와 달라진
boot_id 를 봐야 한다. 시계는 주입한다 (sleep 이 시계를 전진시킨다).
"""
from __future__ import annotations

import math
import time as real_time
from unittest.mock import MagicMock, patch

import pytest

import setup as setup_mod
from setup import BOOT_ID_PATH, BOOT_POLL_INTERVAL, SetupManager

OLD = "11111111-2222-3333-4444-555555555555"
NEW = "66666666-7777-8888-9999-aaaaaaaaaaaa"
BOOT_ID_CMD = f"cat {BOOT_ID_PATH}"


class FakeTime:
    """setup.time 대체 — sleep 이 monotonic 시계를 전진시킨다."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, sec):
        self.now += sec

    def __getattr__(self, name):
        return getattr(real_time, name)


class Board:
    """시간표 보드: [0, down_at) 이전 부팅 응답, [down_at, up_at) 불통, [up_at, ∞) 새 부팅.

    blips: 같은 부팅 안의 일시 불통 구간 [(start, end)].
    boot_id: "always" | "never" | "after_reboot" — boot_id 읽기 가능 여부.
    check_cost: check_connectivity 1회가 쓰는 시간(초) — ConnectTimeout 모사.
    """

    def __init__(self, clock, *, down_at, up_at, blips=(), boot_id="always",
                 check_cost=0.0):
        self.clock = clock
        self.down_at = down_at
        self.up_at = up_at
        self.blips = list(blips)
        self.boot_id = boot_id
        self.check_cost = check_cost
        self.commands: list[str] = []

    def _up(self):
        t = self.clock.now
        if any(s <= t < e for s, e in self.blips):
            return False
        return t < self.down_at or t >= self.up_at

    def check_connectivity(self):
        self.clock.now += self.check_cost
        return self._up()

    def run(self, cmd, **_kw):
        self.commands.append(cmd)
        if not self._up():
            raise ConnectionError("ssh unreachable")
        if cmd == BOOT_ID_CMD:
            new_boot = self.clock.now >= self.up_at
            if self.boot_id == "never":
                return None
            if self.boot_id == "after_reboot" and not new_boot:
                return None
            return NEW if new_boot else OLD
        return ""


@pytest.fixture
def clock():
    fake = FakeTime()
    with patch.object(setup_mod, "time", fake):
        yield fake


def _mgr(board, **kw):
    ssh = MagicMock()
    ssh.check_connectivity.side_effect = board.check_connectivity
    ssh.run.side_effect = board.run
    mgr = SetupManager(ssh, **kw)
    online_at: list[float] = []
    mgr._stabilize = MagicMock(side_effect=lambda _s: online_at.append(board.clock.now))
    mgr._diagnose_network = MagicMock(return_value="unknown")
    return mgr, online_at


def test_old_boot_answering_for_several_polls_is_not_back_online(clock, capsys):
    board = Board(clock, down_at=45, up_at=100)
    mgr, online_at = _mgr(board)

    mgr.reboot_and_wait(stabilize_sec=30)

    assert len(online_at) == 1
    assert board.up_at <= online_at[0] < board.up_at + BOOT_POLL_INTERVAL + 1
    # boot_id 는 reboot **전에** 읽는다
    assert board.commands.index(BOOT_ID_CMD) < board.commands.index("reboot")
    out = capsys.readouterr().out
    assert out.count("old boot still up") >= 3
    assert "reboot pending" in out
    assert "Target back online (after" in out and "new boot" in out


def test_connectivity_lost_then_new_boot_id_is_online_within_one_poll(clock, capsys):
    """기본 생성자(운영 경로)의 폴링 간격이 부팅 감지 지연을 결정한다."""
    board = Board(clock, down_at=5, up_at=73)
    mgr, online_at = _mgr(board)

    mgr.reboot_and_wait(stabilize_sec=30)

    assert len(online_at) == 1
    assert board.up_at <= online_at[0] < board.up_at + BOOT_POLL_INTERVAL + 1
    assert "new boot" in capsys.readouterr().out


def test_same_boot_id_after_network_blip_is_still_old_boot(clock):
    """일시 단절 뒤 이전 부팅이 다시 응답해도 복귀가 아니다 — boot_id 가 우선."""
    board = Board(clock, down_at=60, up_at=120, blips=[(20, 30)])
    mgr, online_at = _mgr(board)

    mgr.reboot_and_wait(stabilize_sec=30)

    assert len(online_at) == 1
    assert online_at[0] >= board.up_at


@pytest.mark.parametrize("boot_id", ["never", "after_reboot"])
def test_unreadable_pre_reboot_boot_id_requires_connectivity_loss(clock, capsys, boot_id):
    board = Board(clock, down_at=45, up_at=100, boot_id=boot_id)
    mgr, online_at = _mgr(board)

    mgr.reboot_and_wait(stabilize_sec=30)

    assert len(online_at) == 1
    assert online_at[0] >= board.up_at
    out = capsys.readouterr().out
    assert "boot_id unavailable" in out
    assert "Target back online (after" in out


def test_reboot_that_never_happens_times_out_after_one_recovery(clock, capsys):
    board = Board(clock, down_at=math.inf, up_at=math.inf)
    mgr, online_at = _mgr(board, reboot_timeout=600)

    with pytest.raises(TimeoutError, match="did not come back online within 600s"):
        mgr.reboot_and_wait(stabilize_sec=30)

    assert online_at == []
    assert mgr._diagnose_network.call_count == 1
    assert "old boot still up" in capsys.readouterr().out


def test_timeout_budget_is_wall_clock_not_sleep_count(clock):
    """보드 불통 시 check 1회가 ConnectTimeout(15s)을 쓴다 — 예산은 실제 경과 시간."""
    board = Board(clock, down_at=0, up_at=math.inf, check_cost=15)
    mgr, online_at = _mgr(board, reboot_timeout=600)

    with pytest.raises(TimeoutError, match="did not come back online within 600s"):
        mgr.reboot_and_wait(stabilize_sec=30)

    assert online_at == []
    assert mgr._diagnose_network.call_count == 1
    # reboot 후 10s + (timeout + 폴 1회분) × 2 라운드(복구 1회 포함)
    per_round = 600 + BOOT_POLL_INTERVAL + board.check_cost
    assert clock.now <= 10 + 2 * per_round
