"""Configured SD cap acceptance is independent of forecast reserve assumptions.

Offline-only; no SSH, board lease, recording, or file mutation.
"""
from dataclasses import replace

import pytest

from scripts.qa_acceptance_safety import Snapshot, GateError, current_sd_limits, storage_gate
from scripts.qa_bounded_4ch_admission import assess_bounded_4ch
from config import load_profile


def sample(**changes):
    # 89.35% used and 898 current files; last board sample was a real
    # read-only observation, but this is purely synthetic test data.
    data = dict(
        used_kib=893500, avail_kib=106500, root_files=898,
        event_files=0, event_kib=4, recycle_kib=120000,
        disk_limit_per=90, disk_limit_file=1000,
        event_storage_size=10, disk_manage=True, event_auto_remove=True,
        recovery_files=0, stale_restart_markers=False,
    )
    data.update(changes)
    return Snapshot(**data)


def test_measured_thresholds_pass_without_reserve():
    status = current_sd_limits(sample())
    assert status["decision"] == "PASS"
    assert status["allowed"] is True
    assert status["usage_within_limit"] and status["root_files_within_limit"]
    assert status["configured_limit_percent"] == 90
    assert status["configured_file_limit"] == 1000
    assert status["execution_authorized"] is False


def test_exact_configured_limits_are_inclusive():
    status = current_sd_limits(sample(used_kib=900000, avail_kib=100000, root_files=1000))
    assert status["decision"] == "PASS"
    assert status["reason_codes"] == []


def test_sub_rounding_overflow_is_not_hidden():
    status = current_sd_limits(sample(used_kib=900001, avail_kib=99999))
    assert status["used_percent_display"] == 90.0
    assert status["decision"] == "FAIL"
    assert status["reason_codes"] == ["CURRENT_SD_USAGE_OVER_LIMIT"]


def test_one_extra_file_exceeds_cap():
    status = current_sd_limits(sample(root_files=1001))
    assert status["decision"] == "FAIL"
    assert status["reason_codes"] == ["CURRENT_ROOT_FILES_OVER_LIMIT"]


def test_both_over_limit_independent():
    status = current_sd_limits(sample(used_kib=910000, avail_kib=90000, root_files=1001))
    assert status["reason_codes"] == [
        "CURRENT_SD_USAGE_OVER_LIMIT", "CURRENT_ROOT_FILES_OVER_LIMIT",
    ]


@pytest.mark.parametrize("change", [
    {"used_kib": True}, {"avail_kib": -1}, {"root_files": -1},
    {"disk_limit_per": 101}, {"disk_limit_per": 0},
    {"disk_limit_file": 0}, {"disk_limit_file": "1000"},
    {"used_kib": 0, "avail_kib": 0},
])
def test_malformed_limits_fail_closed(change):
    with pytest.raises(GateError):
        current_sd_limits(sample(**change))


def test_existing_45min_admission_can_block_while_current_sd_is_pass():
    report = storage_gate(sample())
    assert report["current_sd_limits"]["decision"] == "PASS"
    assert report["allowed"] is False
    assert report["forecast_decision"] == "BLOCK"
    assert "PLANNED_RECORDING_CROSSES_FILE_LIMIT" in report["forecast_reason_codes"]


def test_bounded_4ch_model_uses_same_current_limits_without_authorization():
    profile = load_profile("profiles", case="720p_4ch_mcp_absent")
    report = assess_bounded_4ch(sample(), profile, age_seconds=0,
                               max_extra_segments_per_channel=0)
    assert report["current_sd_limits"]["decision"] == "PASS"
    assert report["forecast_decision"] in ("BLOCK", "CAPACITY_REVIEW_ONLY")
    assert report["execution_authorized"] is False


def test_current_SD_status_does_not_modify_snapshot():
    before = sample()
    _ = current_sd_limits(before)
    assert before == sample()


def test_failing_current_status_remains_separate_from_event_limits():
    status = current_sd_limits(sample(event_files=50000, event_kib=990000))
    assert status["decision"] == "PASS"
    # Event/recovery policy remains part of the *separate* campaign gate.
    assert not storage_gate(sample(event_files=50000, event_kib=990000))["allowed"]
