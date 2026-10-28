"""Offline safety gate: no board, lease, network, storage writes or Plan execution."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
from dataclasses import replace

import pytest

SRC = Path(__file__).resolve().parents[1] / "scripts" / "qa_acceptance_safety.py"
spec = importlib.util.spec_from_file_location("qa_acceptance_safety", SRC)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)


def safe_snapshot():
    return gate.Snapshot(
        150000000, 80000000, 200, 0, 4, 100000, 90, 1000,
        10, True, True, 0, False,
    )


def board_observation():
    return {
        "df": "/dev/mmcblk1p1 244868388 206762580 25597444 89% /mnt/sd_cam",
        "ord": '{"disk_limit_per":90,"disk_limit_file":1000,"disk_manage":true}',
        "camera": '{"event_storage_size":10,"event_auto_remove":true}',
        "root_files": "978", "event_files": "0",
        "event_kib": "4\t/mnt/sd_cam/event",
        "recycle_kib": "203024788\t/mnt/sd_cam/recycle",
        "recovery_files": "0", "markers": "ABSENT",
    }


def test_safe_snapshot_is_safe_without_modifying_a_board():
    assert gate.storage_gate(safe_snapshot())["allowed"]


def test_real_measured_sd_and_root_file_count_block_mutating_campaign():
    out = gate.storage_gate(gate.parse_observation(board_observation()))
    assert not out["allowed"]
    assert out["root_files"] == 978
    assert out["recycle_kib"] == 203024788
    assert {"SD_CAPACITY_MARGIN_INSUFFICIENT", "ROOT_FILE_COUNT_CLOSE_TO_LIMIT",
            "PLANNED_RECORDING_CROSSES_FILE_LIMIT",
            "PLANNED_RECORDING_CROSSES_DISK_LIMIT"} <= set(out["reason_codes"])


@pytest.mark.parametrize("changes,reason", [
    ({"root_files": 999}, "ROOT_FILE_COUNT_CLOSE_TO_LIMIT"),
    ({"event_files": 14999}, "EVENT_FILE_COUNT_CLOSE_TO_LIMIT"),
    ({"event_kib": 26000000}, "EVENT_STORAGE_LIMIT_REACHED"),
    ({"recovery_files": 1}, "DIRTY_RECOVERY_JOURNAL"),
    ({"stale_restart_markers": True}, "STALE_PROCESS_RESTART_MARKERS"),
    ({"disk_manage": False}, "DISK_MANAGEMENT_DISABLED"),
])
def test_hazard_is_independently_rejected(changes, reason):
    result = gate.storage_gate(replace(safe_snapshot(), **changes))
    assert not result["allowed"]
    assert reason in result["reason_codes"]


@pytest.mark.parametrize("field,value", [
    ("df", "/dev/sda 123 12 111 10% /wrong"),
    ("root_files", "-1"),
    ("event_kib", "broken"),
    ("ord", '{"disk_limit_per":90,"disk_limit_file":"1000","disk_manage":true}'),
    ("camera", '{"event_storage_size":0,"event_auto_remove":true}'),
    ("markers", "UNKNOWN"),
])
def test_bad_board_observations_fail_closed(field, value):
    data = board_observation()
    data[field] = value
    with pytest.raises(gate.GateError):
        gate.parse_observation(data)


def test_missing_observation_fails_closed():
    data = board_observation()
    del data["recovery_files"]
    with pytest.raises(gate.GateError):
        gate.parse_observation(data)


def test_host_timeout_does_not_cover_long_lease_recovery():
    r = gate.execution_budget_gate()
    assert not r["allowed"]
    assert r["cleanup_reserved_seconds"] == 1860
    assert "HOST_TIMEOUT_CAN_INTERRUPT_RECOVERY" in r["reason_codes"]


def test_small_lease_fails_to_reserve_duration_plus_cleanup():
    r = gate.execution_budget_gate(7200, 3600, 2700)
    assert "LEASE_EXECUTION_WINDOW_TOO_SHORT" in r["reason_codes"]


def test_independently_supervised_larger_host_budget_is_acceptable():
    assert gate.execution_budget_gate(7200, 5400, 2700)["allowed"]


def test_lease_identity_must_match_exactly_one_active_holder():
    env = {"PIM_BOARD_LOCK_HELD": "1", "PIM_BOARD_LOCK_OWNER_PID": "321",
           "PIM_BOARD_LOCK_BOARD_ID": "pim", "PIM_BOARD_LOCK_SESSION": "test"}
    board = {"result": {"boards": [{"board_id": "pim", "holders": [
        {"session": "test", "mode": "exclusive", "liveness": "alive", "expired": False}]}]}}
    assert gate.lease_is_valid(board, env)
    assert not gate.lease_is_valid(board, {**env, "PIM_BOARD_LOCK_HELD": "0"})
    assert not gate.lease_is_valid(board, {**env, "PIM_BOARD_LOCK_SESSION": "other"})


def test_no_args_is_offline_only(monkeypatch, capsys):
    monkeypatch.setattr(gate, "read_live_board", lambda: pytest.fail("must not use hardware"))
    assert gate.main([]) == 3
    assert '"decision": "BLOCK"' in capsys.readouterr().out


def test_source_pin_requires_clean_two_file_commit(monkeypatch):
    from types import SimpleNamespace
    responses = {
        ("rev-parse", "HEAD^"): gate.BASE_COMMIT,
        ("diff", "--name-only", "HEAD^", "HEAD", "--"): (
            "scripts/qa_acceptance_safety.py\ntests/test_qa_acceptance_safety.py\n"
        ),
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
    }
    def fake_run(argv, **_):
        return SimpleNamespace(stdout=responses[tuple(argv[3:])])
    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.source_is_pinned(Path("/no-real-repository"))
    responses[("status", "--porcelain=v1", "--untracked-files=all")] = " M AGENTS.md"
    assert not gate.source_is_pinned(Path("/no-real-repository"))
    responses[("status", "--porcelain=v1", "--untracked-files=all")] = ""
    responses[("diff", "--name-only", "HEAD^", "HEAD", "--")] += "OTHER.py\n"
    assert not gate.source_is_pinned(Path("/no-real-repository"))


def test_live_path_still_blocks_measured_risk_without_calling_board(monkeypatch, capsys):
    monkeypatch.setattr(gate, "source_is_pinned", lambda *_: True)
    monkeypatch.setattr(gate, "read_live_board",
                        lambda: gate.parse_observation(board_observation()))
    assert gate.main(["--live"]) == 3
    output = __import__("json").loads(capsys.readouterr().out)
    assert output["decision"] == "BLOCK"
    assert "ROOT_FILE_COUNT_CLOSE_TO_LIMIT" in output["storage"]["reason_codes"]
