"""Historical SD budget math: capacity review is never 4ch run authorization."""
from dataclasses import replace
from copy import deepcopy

import pytest

from config import load_profile
from scripts.qa_acceptance_safety import Snapshot
from scripts.qa_bounded_4ch_admission import AdmissionError, assess_bounded_4ch

BASE = load_profile("profiles", case="720p_4ch_mcp_absent")
CH = (("i2c2", "ch0"), ("i2c2", "ch1"),
      ("i2c1", "ch2"), ("i2c1", "ch3"))


def last_observed_snapshot():
    # Offline reconstruction of 2026-10-31T07:11:38Z, not a live board read.
    return Snapshot(
        used_kib=208276120, avail_kib=24083904, root_files=982,
        event_files=0, event_kib=4, recycle_kib=204527720,
        disk_limit_per=90, disk_limit_file=1000, event_storage_size=10,
        disk_manage=True, event_auto_remove=True,
        recovery_files=0, stale_restart_markers=False,
    )


def profile(*, reduced=False, monitor=None):
    p = deepcopy(BASE)
    if reduced:
        for bus, ch in CH:
            p["setup"]["edgeconf_changes"][f".VHL_CAM.{bus}.{ch}.bps"] = [512, 512]
    if monitor is not None:
        p["monitor"]["duration_sec"] = monitor
    return p


def test_original_case_requires_actual_disk_and_file_rotation():
    result = assess_bounded_4ch(last_observed_snapshot(), profile(), age_seconds=0)
    assert result["headroom_to_limit_kib"] == 847901
    assert result["projected_recording_kib"] == 1500000
    assert result["projected_file_count"] == 112
    assert result["required_reclaim_kib"] == 1176388
    assert result["required_root_rotations"] == 111
    assert result["decision"] == "BLOCK"


def test_lower_bitrate_alone_cannot_overcome_root_file_count():
    result = assess_bounded_4ch(
        last_observed_snapshot(), profile(reduced=True), age_seconds=0)
    assert result["recording_kbps"] == 2048
    assert result["projected_recording_kib"] == 200000
    assert result["required_reclaim_kib"] == 0
    assert result["required_root_rotations"] == 111
    assert result["reason_codes"] == ["ROOT_FILE_CAPACITY_WITH_RESERVE"]
    assert result["execution_authorized"] is False


def test_shorter_two_finalizations_candidate_is_still_blocked():
    result = assess_bounded_4ch(
        last_observed_snapshot(), profile(reduced=True, monitor=120),
        age_seconds=0)
    assert result["writer_exposure_seconds"] == 620
    assert result["projected_recording_kib"] == 155000
    assert result["projected_file_count"] == 88
    assert result["required_root_rotations"] == 87
    assert result["decision"] == "BLOCK"


def test_eligible_root_rotation_is_required_not_assumed():
    original = last_observed_snapshot()
    observed_after_rotation = replace(original, root_files=895)
    result = assess_bounded_4ch(
        observed_after_rotation, profile(reduced=True, monitor=120),
        age_seconds=0)
    assert result["root_files_after_projection"] == 983
    assert result["decision"] == "CAPACITY_REVIEW_ONLY"
    assert result["execution_authorized"] is False
    assert result["prune_causality_verified"] is False
    assert result["source_and_binary_identity_verified"] is False


def test_stale_record_cannot_authorize_minimal_case():
    with pytest.raises(AdmissionError):
        assess_bounded_4ch(
            last_observed_snapshot(), profile(reduced=True, monitor=120),
            age_seconds=121)
