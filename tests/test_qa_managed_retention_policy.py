"""Offline ORD retention evidence gate; never uses SSH or sends packets."""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qa_managed_retention_policy import ManagedRetentionEvidence, assess_managed_retention
from qa_acceptance_safety import Snapshot


def measured():
    return Snapshot(
        208882236, 23477788, 934, 0, 4, 205354968, 90, 1000,
        5, True, True, 0, False,
    )


def verified():
    return ManagedRetentionEvidence(
        installed_retention_source_attested=True,
        merged_runtime_attested=True,
        exclusive_board_lease_attested=True,
        retention_cleanup_scope_verified=True,
        recording_writer_lifecycle_verified=True,
        fd_visibility_verified=True,
        automatic_rotation_and_prune_observed=True,
        storage_mount_stable_verified=True,
        deletion_scope_authorized_for_this_campaign=True,
        full_campaign_restore_runner_verified=True,
        observation_age_seconds=5,
        eligible_closed_root_files=500,
        eligible_recycle_reclaim_kib=15_000_000,
        cleanup_period_seconds=40,
        max_rotated_files_per_pass=32,
    )


def test_measured_board_blocks_without_attestations():
    result = assess_managed_retention(measured(), ManagedRetentionEvidence())
    assert result["decision"] == "BLOCK"
    assert not result["execution_authorized"]
    for code in (
        "INSTALLED_RETENTION_UNVERIFIED", "WRITER_LIFECYCLE_UNVERIFIED",
        "DURABLE_RESTORE_RUNNER_UNVERIFIED", "RECLAIMABLE_RECYCLE_UNVERIFIED"
    ):
        assert code in result["reason_codes"]


def test_complete_synthetic_proof_still_cannot_authorize_board():
    result = assess_managed_retention(measured(), verified())
    assert result["decision"] == "REVIEW_REQUIRED"
    assert not result["execution_authorized"]
    assert result["reason_codes"] == ()
    assert result["root_rotations_required"] == 394
    assert result["projected_file_count"] == 360
    assert result["recycle_reclaim_kib_required"] > 0
    assert result["root_rotations_possible_by_period"] == 67 * 32


@pytest.mark.parametrize("name,reason", [
    ("installed_retention_source_attested", "INSTALLED_RETENTION_UNVERIFIED"),
    ("recording_writer_lifecycle_verified", "WRITER_LIFECYCLE_UNVERIFIED"),
    ("fd_visibility_verified", "FD_VISIBILITY_UNVERIFIED"),
    ("retention_cleanup_scope_verified", "CLEANUP_SCOPE_UNVERIFIED"),
    ("full_campaign_restore_runner_verified", "DURABLE_RESTORE_RUNNER_UNVERIFIED"),
    ("deletion_scope_authorized_for_this_campaign", "CLEANUP_NOT_AUTHORIZED"),
])
def test_missing_attestation_blocks(name,reason):
    result=assess_managed_retention(measured(),replace(verified(),**{name:None}))
    assert result["decision"]=="BLOCK"
    assert reason in result["reason_codes"]


def test_fd_observation_is_not_writer_close_proof():
    result=assess_managed_retention(
        measured(),replace(verified(),recording_writer_lifecycle_verified=False))
    assert "WRITER_LIFECYCLE_UNVERIFIED" in result["reason_codes"]


def test_recycle_size_is_not_prunable_capacity():
    result=assess_managed_retention(
        measured(),replace(verified(),eligible_recycle_reclaim_kib=None))
    assert "RECLAIMABLE_RECYCLE_UNVERIFIED" in result["reason_codes"]


def test_fake_recycle_capacity_exceeding_observed_is_rejected():
    result=assess_managed_retention(
        measured(),replace(verified(),eligible_recycle_reclaim_kib=300_000_000))
    assert "RECLAIMABLE_RECYCLE_UNVERIFIED" in result["reason_codes"]


def test_insufficient_proven_recycle_and_root_capacity():
    proof=replace(verified(),eligible_recycle_reclaim_kib=1000,
                  eligible_closed_root_files=100)
    result=assess_managed_retention(measured(),proof)
    assert "RECLAIMABLE_RECYCLE_INSUFFICIENT" in result["reason_codes"]
    assert "ROOT_ROTATION_CAPACITY_INSUFFICIENT" in result["reason_codes"]


def test_cleanup_throughput_must_cover_root_rotations():
    result=assess_managed_retention(
        measured(),replace(verified(),cleanup_period_seconds=1350,
                            max_rotated_files_per_pass=32))
    assert "RETENTION_THROUGHPUT_INSUFFICIENT" in result["reason_codes"]


def test_snapshot_older_than_120_seconds_is_rejected():
    result=assess_managed_retention(
        measured(),replace(verified(),observation_age_seconds=121))
    assert "OBSERVATION_NOT_FRESH" in result["reason_codes"]


def test_dirty_journal_and_disk_manage_disabled_block():
    result=assess_managed_retention(
        replace(measured(),disk_manage=False,recovery_files=1),verified())
    assert "DISK_MANAGEMENT_DISABLED" in result["reason_codes"]
    assert "RECOVERY_JOURNAL_UNCLEAN" in result["reason_codes"]


def test_physical_space_shortage_is_not_hidden_by_automatic_cleanup():
    result=assess_managed_retention(
        measured(),replace(verified(),eligible_recycle_reclaim_kib=1000),
        projected_recording_kib=30_000_000)
    assert "PHYSICAL_SPACE_INSUFFICIENT" in result["reason_codes"]


@pytest.mark.parametrize("name,value",[
    ("used_kib",True),("root_files",-1),("disk_limit_file",0),
    ("disk_limit_per",101),("disk_manage",1),
])
def test_malformed_observations_rejected(name,value):
    with pytest.raises(ValueError):
        assess_managed_retention(replace(measured(),**{name:value}),verified())


def test_policy_has_no_board_execution_api():
    import qa_managed_retention_policy as policy
    assert not hasattr(policy,"read_live_board")
    assert not hasattr(policy,"execute_cleanup")


@pytest.mark.parametrize("duration,expected_count", [
    (1, 8), (60, 8), (61, 16), (2700, 360),
])
def test_projected_root_entries_include_mp4_and_srt(duration, expected_count):
    result = assess_managed_retention(
        measured(), verified(), expected_duration_seconds=duration,
        projected_recording_kib=1000,
    )
    assert result["projected_file_count"] == expected_count


def test_closed_file_evidence_cannot_be_segment_count_only():
    result = assess_managed_retention(
        measured(), replace(verified(), eligible_closed_root_files=393),
    )
    assert result["root_rotations_required"] == 394
    assert result["decision"] == "BLOCK"
    assert "ROOT_ROTATION_CAPACITY_INSUFFICIENT" in result["reason_codes"]
