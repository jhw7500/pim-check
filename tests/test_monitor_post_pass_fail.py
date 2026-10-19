"""
tests/test_monitor_post_pass_fail.py — full monitor 의 post-pass 실패 보존 (#170).

full monitor(`monitor_until_pass=False`)는 첫 전-체크 통과 뒤의 이탈(drift)을 잡으려고
창 끝까지 관측한다. 그런데 merge 는 체크별 **가장 늦은** 실패만 남기고, verify retry 는
실패 reason 에 안정화 지표(FAIL:NO_BR 등)가 하나라도 있으면 monitor 결과 전체를 단일
retry snapshot 으로 바꾼다. 그래서 통과 뒤의 실제 실패가 나중의 일시 실패에 덮이고 retry
통과로 사라질 수 있었다.

규칙: 첫 전-체크 통과 snapshot **이후**에 나온 비-안정화 실패는 merge 와 retry 를 지나
남는다(custom_commands 는 항목 단위로 분류). 통과 전(부팅·안정화 구간)의 실패와 어디서든
안정화 지표로 분류되는 실패는 지금처럼 retry 로 회복된다. until_pass 경로는 바뀌지 않는다.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from checks.custom import CustomCommandCheck
from engine import Engine
from verify_retry import run_verify_with_retry


def _custom(ch0: str = "OK", ch1: str = "OK") -> dict:
    """custom_commands 결과 — 항목 출력으로 실제 validate 를 거쳐 reason 을 만든다."""
    data = {"skipped": False, "results": [
        {"name": "ch0 BITRATE", "command": "true", "output": ch0, "expected": "OK",
         "expected_min": None, "on_fail": "ch0 bitrate out of range"},
        {"name": "ch1 BITRATE", "command": "true", "output": ch1, "expected": "OK",
         "expected_min": None, "on_fail": "ch1 bitrate out of range"},
    ]}
    passed, reason = CustomCommandCheck().validate(data, {})
    return {"name": "custom_commands", "passed": passed, "reason": reason, "data": data}


def _thermal(temp: int | None = None) -> dict:
    if temp is None:
        return {"name": "thermal", "passed": True, "reason": "OK", "data": {}}
    return {"name": "thermal", "passed": False,
            "reason": f"Temperature {temp} > max 85", "data": {}}


def _snap(ch0: str = "OK", ch1: str = "OK", temp: int | None = None) -> list:
    return [_thermal(temp), _custom(ch0, ch1)]


def _run(monitor_snaps: list, retry_snaps: list, *, until_pass: bool = False):
    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    duration = len(monitor_snaps)  # interval 1 → samples_total == 관측 snapshot 수
    profile = {"monitor": {"duration_sec": duration, "interval_sec": 1}, "checks": {}}
    engine = Engine(ssh, profile)
    engine.run_snapshot = MagicMock(side_effect=list(monitor_snaps) + list(retry_snaps))
    with patch("engine.time.sleep"), patch("verify_retry.time.sleep"), \
            patch("verify_retry.MAX_ATTEMPTS", 3):
        results, _collected, _total = run_verify_with_retry(
            engine, ssh, duration, until_pass=until_pass)
    return results, engine.run_snapshot.call_count


def _failed(results: list) -> dict:
    return {r["name"]: r for r in results if not r["passed"]}


# ── 통과 뒤 비-안정화 실패는 retry 통과로 지워지지 않는다 ───────────────

def test_post_pass_hard_item_survives_later_transient_and_passing_retry():
    """통과 → ch0 실제 이탈 → (나중) ch1 일시 NO_BR → retry 통과여도 case 는 fail."""
    results, _calls = _run(
        [_snap(), _snap(ch0="FAIL:148kbps"), _snap(ch1="FAIL:NO_BR")],
        [_snap(), _snap()],
    )
    failed = _failed(results)
    assert set(failed) == {"custom_commands"}
    reason = failed["custom_commands"]["reason"]
    # 이탈 항목을 이름으로 밝히고, 첫 통과 뒤 관측임을 말한다.
    assert "ch0 BITRATE" in reason and "FAIL:148kbps" in reason
    assert "after first all-pass" in reason
    # 안정화 지표 항목(ch1 NO_BR)은 고정 대상이 아니다.
    assert "ch1 BITRATE" not in reason
    assert failed["custom_commands"]["post_pass_fail"] == [
        "ch0 BITRATE: ch0 bitrate out of range (got: FAIL:148kbps)"]


def test_post_pass_hard_and_transient_items_in_same_sample():
    """한 snapshot 에 실제 이탈(ch0)과 일시 NO_BR(ch1)이 함께 → 항목 단위로 ch0 만 고정."""
    results, _calls = _run(
        [_snap(), _snap(ch0="FAIL:148kbps", ch1="FAIL:NO_BR")],
        [_snap(), _snap()],
    )
    failed = _failed(results)
    assert set(failed) == {"custom_commands"}
    reason = failed["custom_commands"]["reason"]
    assert "ch0 BITRATE" in reason and "ch1 BITRATE" not in reason


def test_post_pass_hard_failure_of_other_check_survives_retry():
    """다른 체크(thermal)의 통과 뒤 실패도 custom 의 일시 NO_BR 이 부른 retry 로 사라지지 않는다."""
    results, _calls = _run(
        [_snap(), _snap(temp=90), _snap(ch1="FAIL:NO_BR")],
        [_snap(), _snap()],
    )
    failed = _failed(results)
    assert set(failed) == {"thermal"}
    assert "Temperature 90 > max 85" in failed["thermal"]["reason"]
    assert "after first all-pass" in failed["thermal"]["reason"]


# ── 기존 동작 보존 ─────────────────────────────────────────────────

def test_pre_pass_failure_then_pass_still_passes():
    """첫 통과 전(부팅 구간)의 실패는 고정하지 않는다 — 지금처럼 retry 로 회복."""
    results, _calls = _run(
        [_snap(ch0="FAIL:148kbps", ch1="FAIL:NO_BR"), _snap(), _snap()],
        [_snap()],
    )
    assert _failed(results) == {}
    assert not any("post_pass_fail" in r for r in results)


@pytest.mark.parametrize("transient", ["FAIL:NO_BR", "NEED_2_FINALIZES"])
def test_post_pass_stabilization_only_failure_recovers(transient):
    """통과 뒤라도 안정화 지표만 있는 실패(세그먼트 경계 NO_BR 등)는 retry 로 회복."""
    results, calls = _run([_snap(), _snap(ch1=transient), _snap()], [_snap()])
    assert _failed(results) == {}
    assert calls == 4  # monitor 3 + retry 1


def test_until_pass_unchanged_returns_first_all_pass_snapshot():
    """until_pass 는 첫 전-체크 통과 snapshot 에서 끝나므로 '통과 뒤' 관측이 없다."""
    results, calls = _run(
        [_snap(ch0="FAIL:148kbps", ch1="FAIL:NO_BR"), _snap(), _snap(ch0="FAIL:148kbps")],
        [],
        until_pass=True,
    )
    assert calls == 2
    assert _failed(results) == {}
    assert not any("post_pass_fail" in r for r in results)


def test_merge_without_any_all_pass_snapshot_keeps_latest_failure():
    """전-체크 통과 snapshot 이 없으면 merge 는 지금처럼 체크별 가장 늦은 실패를 남긴다."""
    engine = Engine(MagicMock(), {"monitor": {"duration_sec": 0, "interval_sec": 1}})
    merged = engine.merge_snapshots([_snap(ch0="FAIL:148kbps"), _snap(ch1="FAIL:NO_BR")])
    custom = next(r for r in merged if r["name"] == "custom_commands")
    assert custom["passed"] is False
    assert "ch1 BITRATE" in custom["reason"] and "ch0 BITRATE" not in custom["reason"]
    assert "post_pass_fail" not in custom
