"""
tests/test_monitor_cap_engine.py — plan monitor_cap_sec 가 engine monitor 루프를 실제로 줄인다 (#171).

plan.py 는 cap 으로 줄인 effective_duration 을 run_verify_with_retry 에 넘기지만, engine 이
profile 의 monitor.duration_sec 을 다시 읽으면 cap 은 0(snapshot) 일 때만 효과가 있다.
기존 TestMonitorCap(test_plan_execute.py)은 넘어간 인자만 보므로, 여기서는 실제 Engine
루프를 가짜 시계로 돌려 snapshot 시각을 관측한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import engine as engine_mod
from engine import Engine
from plan import _run_single_case


class FakeClock:
    """engine.time 대체 — sleep 이 time() 시계를 전진시킨다."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, sec):
        self.now += sec


def _run_case(profile_duration: int, cap: int | None, interval: int = 5):
    clock = FakeClock()
    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    profile = {"monitor": {"duration_sec": profile_duration, "interval_sec": interval},
               "checks": {}}
    taken: list[float] = []
    engines: list[Engine] = []

    def _snapshot():
        taken.append(clock.now)
        return [{"name": "x", "passed": True, "reason": "OK"}]

    def _factory(s, p):
        eng = Engine(s, p)
        eng.run_snapshot = MagicMock(side_effect=_snapshot)
        engines.append(eng)
        return eng

    with patch.object(engine_mod, "time", clock):
        _results, passed, error = _run_single_case(
            ssh, profile, "c", _factory, None, monitor_cap_sec=cap)
    assert (passed, error) == (True, None)
    return taken


def test_cap_shortens_engine_monitor_loop():
    """profile 300s + cap 150s → monitor 는 150s 창에서 끝난다 (interval 5 → 30 samples)."""
    taken = _run_case(300, 150)
    assert len(taken) == 150 // 5
    assert max(taken) < 150


def test_cap_none_keeps_profile_duration():
    taken = _run_case(300, None)
    assert len(taken) == 300 // 5
    assert 150 <= max(taken) < 300


def test_cap_longer_than_profile_does_not_extend():
    taken = _run_case(100, 150)
    assert len(taken) == 100 // 5
    assert max(taken) < 100


def test_profile_zero_stays_single_snapshot():
    """profile 0(snapshot) 은 cap 이 있어도 monitor 없이 snapshot 1회."""
    taken = _run_case(0, 150)
    assert taken == [0.0]
