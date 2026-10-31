"""Review-only low bitrate 4ch candidate: no SSH or real board execution."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from config import load_profile
from scripts.qa_acceptance_safety import Snapshot
from scripts.qa_bounded_4ch_admission import AdmissionError, assess_bounded_4ch
from tests.test_cases_mcp_absent_contract import UniqueLoader

ROOT = Path(__file__).resolve().parents[1]
BASE = "720p_4ch_mcp_absent"
CANDIDATE = "720p_4ch_mcp_absent_lowrate_120s"
CHANNELS = (("i2c2", "ch0"), ("i2c2", "ch1"),
            ("i2c1", "ch2"), ("i2c1", "ch3"))


def raw(name):
    path = ROOT / "profiles" / "cases" / f"{name}.yaml"
    return yaml.load(path.read_text(encoding="utf8"), Loader=UniqueLoader)


def profile():
    return load_profile(str(ROOT / "profiles"), case=CANDIDATE)


def historical_snapshot():
    """Observed 2026-10-31T09:23Z, NEVER a current board admission."""
    return Snapshot(
        used_kib=207467396, avail_kib=24892628,
        root_files=886, event_files=0, event_kib=4,
        recycle_kib=204079612, disk_limit_per=90,
        disk_limit_file=1000, event_storage_size=10,
        disk_manage=True, event_auto_remove=True,
        recovery_files=0, stale_restart_markers=False,
    )


def test_candidate_is_explicit_non_smoke_and_target_is_pinned():
    row = profile()
    tags = row["tags"]
    assert "camera" in tags and "mcp_absent" in tags
    assert "experimental_lowrate" in tags and "capacity_review_only" in tags
    assert "smoke" not in tags and "fault" not in tags
    assert row["target"]["host"] == "192.168.214.4"
    assert "미검증" in row["description"]


def test_only_intended_semantic_profile_fields_differ_from_original():
    standard, short = deepcopy(raw(BASE)), deepcopy(raw(CANDIDATE))
    assert all(isinstance(x, dict) for x in (standard, short))
    # Exact intentional allowlist, so a future config drift fails CI.
    for key in ("name", "description", "tags"):
        standard[key] = short[key]
    for bus, ch in CHANNELS:
        key = f".VHL_CAM.{bus}.{ch}.bps"
        assert standard["setup"]["edgeconf_changes"][key][0] > 512
        standard["setup"]["edgeconf_changes"][key] = [512, 512]
    assert "monitor" not in standard
    standard["monitor"] = {"duration_sec": 120}
    assert standard == short


def test_all_led_off_four_channels_and_final_recording_are_still_required():
    candidate = profile()
    changes = candidate["setup"]["edgeconf_changes"]
    assert candidate["setup"]["reboot_after"] is True
    assert candidate["setup"]["stabilize_sec"] == 200
    assert candidate["monitor"]["duration_sec"] == 120
    assert candidate["monitor"]["interval_sec"] > 0
    assert candidate["setup"]["ord_vcm_changes"] == {".VCM.srt_enable": True}
    assert changes[".VHL_CAM.cam_width"] == 1280
    assert changes[".VHL_CAM.cam_height"] == 720
    assert changes[".VHL_CAM.fps"] == 30
    assert changes[".VHL_CAM.recording_time"] == 1
    for bus, ch in CHANNELS:
        prefix = f".VHL_CAM.{bus}.{ch}"
        assert changes[prefix+".enable"] is True
        assert changes[prefix+".led_flash.enable"] is False
        assert changes[prefix+".bps"] == [512, 512]
    assert candidate["checks"]["frame_flow"]["enabled"] is True
    assert candidate["checks"]["recording"]["expected_channels"] == 4
    assert candidate["checks"]["recording"]["session_progress"] == "required"


def test_custom_commands_are_unchanged_and_source_has_strict_srt_anchor():
    candidate = profile()
    old = load_profile(str(ROOT / "profiles"), case=BASE)
    assert candidate["checks"]["custom_commands"] == old["checks"]["custom_commands"]
    strict_srt = [
        row for row in candidate["checks"]["custom_commands"]
        if "mp4와 srt sync" in row.get("name", "")
    ]
    assert len(strict_srt) == 1
    cmd = strict_srt[0]["command"]
    assert "FAIL:NEED_2_FINALIZES_AFTER_BOOT" in cmd
    assert "uptime -s" in cmd
    assert ".part" in cmd


def test_existing_equipped_board_profile_remains_unchanged():
    original = raw("720p_4ch")
    absent = raw(BASE)
    new = raw(CANDIDATE)
    assert original["setup"]["edgeconf_changes"][".VHL_CAM.i2c2.ch0.led_flash.enable"] is True
    assert absent["setup"]["edgeconf_changes"][".VHL_CAM.i2c2.ch0.led_flash.enable"] is False
    assert new["setup"]["edgeconf_changes"][".VHL_CAM.i2c2.ch0.led_flash.enable"] is False


def test_historical_zero_extra_segment_assumption_is_not_mutation_authorization():
    # Zero extra segments is an explicit synthetic assumption, not a live proof.
    report = assess_bounded_4ch(historical_snapshot(), profile(),
                               age_seconds=0, max_extra_segments_per_channel=0)
    assert report["writer_exposure_seconds"] == 620
    assert report["recording_kbps"] == 2048
    assert report["projected_recording_kib"] == 155000
    assert report["projected_file_count"] == 88
    assert report["root_files_after_projection"] == 974
    assert report["reason_codes"] == []
    assert report["decision"] == "CAPACITY_REVIEW_ONLY"
    assert report["execution_authorized"] is False
    assert report["prune_causality_verified"] is False
    assert report["writer_eos_or_fsync_verified"] is False


@pytest.mark.parametrize("root_files", [982, 990, 999])
def test_overfilled_root_remains_blocked_even_with_low_bitrate(root_files):
    from dataclasses import replace
    snapshot = replace(historical_snapshot(), root_files=root_files)
    result = assess_bounded_4ch(snapshot, profile(), age_seconds=0)
    assert result["decision"] == "BLOCK"
    assert result["required_root_rotations"] > 0


@pytest.mark.parametrize("bad_key", [
    ".VHL_CAM.i2c2.ch0.led_flash.enable",
    ".VHL_CAM.i2c1.ch2.led_flash.enable",
])
def test_led_reenable_cannot_pass_capacity_gate(bad_key):
    cfg = deepcopy(profile())
    cfg["setup"]["edgeconf_changes"][bad_key] = True
    with pytest.raises(AdmissionError):
        assess_bounded_4ch(historical_snapshot(), cfg, age_seconds=0)


@pytest.mark.parametrize("unknown_age", [121, -1, True, None])
def test_no_stale_historical_sample_can_claim_review(unknown_age):
    with pytest.raises(AdmissionError):
        assess_bounded_4ch(historical_snapshot(), profile(),
                           age_seconds=unknown_age)


def test_missing_restart_bound_blocks_review_at_historical_root():
    """Do not treat nominal 11 segments as a true upper bound without a restart cap."""
    report = assess_bounded_4ch(historical_snapshot(), profile(), age_seconds=0)
    assert report["projected_file_count"] == 88
    assert report["decision"] == "BLOCK"
    assert "EXTRA_SEGMENT_BOUND_MISSING" in report["reason_codes"]
    assert report["execution_authorized"] is False


def test_one_bounded_extra_segment_per_channel_is_only_reviewable():
    """One interruption can cost 8 MP4/SRT root entries; still no run authorization."""
    report = assess_bounded_4ch(historical_snapshot(), profile(),
                               age_seconds=0, max_extra_segments_per_channel=1)
    assert report["nominal_recording_segments_per_channel"] == 11
    assert report["max_extra_segments_per_channel"] == 1
    assert report["projected_file_count"] == 96
    assert report["root_files_after_projection"] == 982
    assert report["decision"] == "CAPACITY_REVIEW_ONLY"
    assert report["execution_authorized"] is False


def test_two_extra_recording_segments_exceed_sixteen_file_reserve():
    """Two interrupted recording sessions add 16 entries, breaking historical cap."""
    report = assess_bounded_4ch(historical_snapshot(), profile(),
                               age_seconds=0, max_extra_segments_per_channel=2)
    assert report["projected_file_count"] == 104
    assert report["root_files_after_projection"] == 990
    assert report["required_root_rotations"] == 7
    assert report["decision"] == "BLOCK"
    assert "ROOT_FILE_CAPACITY_WITH_RESERVE" in report["reason_codes"]
    assert report["execution_authorized"] is False


@pytest.mark.parametrize("invalid", [True, False, -1, 0.5, "2"])
def test_noninteger_or_negative_restart_segment_bounds_fail_closed(invalid):
    with pytest.raises(AdmissionError):
        assess_bounded_4ch(historical_snapshot(), profile(),
                          age_seconds=0, max_extra_segments_per_channel=invalid)
