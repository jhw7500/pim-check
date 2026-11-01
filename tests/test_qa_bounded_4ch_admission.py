"""Capacity-only admission for the MCP4018-absent 4ch profile (offline)."""
from copy import deepcopy

import pytest

from config import load_profile
from scripts.qa_acceptance_safety import Snapshot
from scripts.qa_bounded_4ch_admission import AdmissionError, assess_bounded_4ch

PROFILE = load_profile("profiles", case="720p_4ch_mcp_absent")


def observed():
    # Synthetic fixture reconstructed from 2026-10-31 15:55 KST gate:
    # 89.58% used, 968081 KiB to 90%, 950/1000 root files.
    capacity = 232360020
    used = capacity * 90 // 100 - 968081
    return Snapshot(
        used_kib=used, avail_kib=capacity-used, root_files=950,
        event_files=0, event_kib=4, recycle_kib=204527720,
        disk_limit_per=90, disk_limit_file=1000,
        event_storage_size=10, disk_manage=True, event_auto_remove=True,
        recovery_files=0, stale_restart_markers=False,
    )


def test_profile_uses_existing_real_four_channel_rates():
    result = assess_bounded_4ch(observed(), PROFILE, age_seconds=0)
    assert result["recording_kbps"] == 15360
    assert result["setup_stabilize_seconds"] == 200
    assert result["monitor_seconds"] == 300
    assert result["restore_window_seconds"] == 300
    assert result["writer_exposure_seconds"] == 800


def test_actual_snapshot_blocks_current_profile_without_pretend_prune():
    report = assess_bounded_4ch(observed(), PROFILE, age_seconds=0)
    assert report["decision"] == "BLOCK"
    assert report["execution_authorized"] is False
    assert "INSUFFICIENT_SD_HEADROOM_WITH_RESERVE" in report["reason_codes"]
    assert "ROOT_FILE_CAPACITY_WITH_RESERVE" in report["reason_codes"]
    assert report["root_files_after_projection"] > observed().disk_limit_file
    assert report["required_reclaim_kib"] > 0
    assert report["required_root_rotations"] > 0
    assert report["recycle_kib_observed_not_reclaimable_proof"] == observed().recycle_kib


def test_profile_forecast_includes_mp4_and_srt_per_channel_conservatively():
    r = assess_bounded_4ch(observed(), PROFILE, age_seconds=0)
    assert r["projected_recording_kib"] == 1_500_000
    assert r["projected_file_count"] == 4 * 2 * 14
    assert r["required_root_rotations"] == 79  # must remain strictly below threshold


def test_available_disk_not_the_same_as_headroom_to_ord_threshold():
    source = observed()
    assert source.avail_kib > 2_000_000
    r = assess_bounded_4ch(source, PROFILE, age_seconds=0)
    assert r["headroom_to_limit_kib"] == 968081
    assert "INSUFFICIENT_SD_HEADROOM_WITH_RESERVE" in r["reason_codes"]


def test_spacious_offline_fixture_is_review_only_not_execution_permission():
    src = observed()
    generous = Snapshot(
        used_kib=40_000_000, avail_kib=110_000_000, root_files=100,
        event_files=src.event_files, event_kib=src.event_kib,
        recycle_kib=src.recycle_kib, disk_limit_per=src.disk_limit_per,
        disk_limit_file=src.disk_limit_file,
        event_storage_size=src.event_storage_size,
        disk_manage=src.disk_manage, event_auto_remove=src.event_auto_remove,
        recovery_files=0, stale_restart_markers=False)
    r = assess_bounded_4ch(generous, PROFILE, age_seconds=0,
                          max_extra_segments_per_channel=0)
    assert r["decision"] == "CAPACITY_REVIEW_ONLY"
    assert not r["reason_codes"]
    assert r["execution_authorized"] is False
    assert r["board_touched"] is False


@pytest.mark.parametrize("corrupt", [
    ("LED", ".VHL_CAM.i2c2.ch0.led_flash.enable", True),
    ("CHANNEL", ".VHL_CAM.i2c1.ch2.enable", False),
    ("BITRATE", ".VHL_CAM.i2c1.ch3.bps", [0, 0]),
    ("BITRATE_BOOL", ".VHL_CAM.i2c1.ch3.bps", [True, 8192]),
])
def test_incorrect_camera_profile_is_refused(corrupt):
    _, key, value = corrupt
    profile = deepcopy(PROFILE)
    profile["setup"]["edgeconf_changes"][key] = value
    with pytest.raises(AdmissionError):
        assess_bounded_4ch(observed(), profile, age_seconds=0)


def test_wrong_target_and_nonbool_srt_refused():
    profile = deepcopy(PROFILE)
    profile["target"]["host"] = "192.168.0.5"
    with pytest.raises(AdmissionError):
        assess_bounded_4ch(observed(), profile, age_seconds=0)
    profile = deepcopy(PROFILE)
    profile["setup"]["ord_vcm_changes"][".VCM.srt_enable"] = False
    with pytest.raises(AdmissionError):
        assess_bounded_4ch(observed(), profile, age_seconds=0)


@pytest.mark.parametrize("age", [121, -1, True, "0", None])
def test_stale_or_invalid_observation_refuses_safety_claim(age):
    with pytest.raises(AdmissionError):
        assess_bounded_4ch(observed(), PROFILE, age_seconds=age)


@pytest.mark.parametrize("corrupt", [
    {"disk_manage": False},
    {"recovery_files": 1},
    {"stale_restart_markers": True},
])
def test_untrusted_storage_policy_blocks_without_moving_files(corrupt):
    fields = {**observed().__dict__, **corrupt}
    s = Snapshot(**fields)
    r = assess_bounded_4ch(s, PROFILE, age_seconds=0)
    assert r["decision"] == "BLOCK"
    assert r["execution_authorized"] is False


def test_shorter_bound_may_fit_in_storage_but_still_requires_owner_review():
    profile = deepcopy(PROFILE)
    profile["setup"]["stabilize_sec"] = 1
    profile["monitor"]["duration_sec"] = 100
    r = assess_bounded_4ch(
        observed(), profile, age_seconds=0, restore_window_seconds=20,
        max_extra_segments_per_channel=0)
    assert r["decision"] == "CAPACITY_REVIEW_ONLY"
    assert r["projected_file_count"] == 4 * 2 * 3
    assert r["execution_authorized"] is False


def test_invalid_restore_window_and_reserve_are_refused():
    for field in ({"restore_window_seconds": 0}, {"restore_window_seconds": True},
                  {"reserve_kib": -1}, {"file_reserve": "10"}):
        with pytest.raises(AdmissionError):
            assess_bounded_4ch(observed(), PROFILE, age_seconds=0, **field)


def test_module_never_calls_board_and_has_no_mutating_api():
    from scripts import qa_bounded_4ch_admission as mod
    assert not hasattr(mod, "SshClient")
    assert not hasattr(mod, "subprocess")
    assert not hasattr(mod, "apply_changes")
    assert not hasattr(mod, "reboot_and_wait")
