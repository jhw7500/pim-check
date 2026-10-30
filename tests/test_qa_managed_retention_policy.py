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
        automatic_root_rotation_observed=True,
        automatic_recycle_prune_observed=True,
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


# Evidence from the 2026-10-30 passive ORD run is a *fixture*, not permission
# to rotate/delete a file or run an active 45-minute campaign.
def actual_ord_samples():
    from qa_managed_retention_policy import PassiveRetentionSample

    return [
        PassiveRetentionSample("2026-10-30T02:20:40Z", 1000, 500, 500, 282,
                               204057820, 207878052, 24481972),
        PassiveRetentionSample("2026-10-30T02:21:26Z", 1002, 501, 501, 282,
                               204057820, 207885652, 24474372),
        PassiveRetentionSample("2026-10-30T02:22:13Z", 884, 442, 442, 283,
                               204508748, 207893252, 24466772),
    ]


def latest_actual_snapshot():
    return replace(measured(), used_kib=207893252, avail_kib=24466772,
                   root_files=884, recycle_kib=204508748)


def test_actual_board_samples_corroborate_rotation_but_not_prune():
    from qa_managed_retention_policy import assess_passive_retention

    result = assess_passive_retention(actual_ord_samples(), file_limit=1000,
                                      disk_limit_per=90)
    assert result["root_rotation_corroborated"] is True
    assert result["rotation_root_files"] == 118
    assert result["rotation_mp4_files"] == 59
    assert result["rotation_srt_files"] == 59
    assert result["recycle_growth_kib"] == 450928
    assert result["recycle_prune_corroborated"] is False
    assert result["effective_used_percent"] == pytest.approx(89.47, abs=0.01)
    assert result["execution_authorized"] is False
    assert result["installed_source_attested"] is False
    assert result["writer_lifecycle_attested"] is False


def test_root_drop_without_recycle_gain_cannot_correlate_a_rotation():
    from qa_managed_retention_policy import assess_passive_retention
    sample = actual_ord_samples()
    after = replace(sample[2], recycle_kib=sample[1].recycle_kib,
                    recycle_dirs=sample[1].recycle_dirs)
    result = assess_passive_retention([sample[1], after], file_limit=1000,
                                      disk_limit_per=90)
    assert result["root_rotation_corroborated"] is False


def test_wide_gap_does_not_attribute_an_old_rotation():
    from qa_managed_retention_policy import assess_passive_retention
    sample = actual_ord_samples()
    after = replace(sample[2], utc="2026-10-30T03:22:13Z")
    result = assess_passive_retention([sample[1], after], file_limit=1000,
                                      disk_limit_per=90)
    assert result["root_rotation_corroborated"] is False


@pytest.mark.parametrize("field,value", [
    ("utc", "not-a-timestamp"), ("root_files", -1),
    ("mp4_files", True), ("used_kib", -100), ("recycle_dirs", 1.5),
])
def test_bad_passive_observation_fails_closed(field, value):
    from qa_managed_retention_policy import assess_passive_retention
    sample = actual_ord_samples()
    with pytest.raises(ValueError):
        assess_passive_retention([sample[0], replace(sample[1], **{field: value})],
                                 file_limit=1000, disk_limit_per=90)


def test_reversed_timestamps_fail_closed_not_corroborated():
    from qa_managed_retention_policy import assess_passive_retention
    sample = actual_ord_samples()
    with pytest.raises(ValueError):
        assess_passive_retention([sample[2], sample[1]], file_limit=1000,
                                 disk_limit_per=90)


def test_reclaim_signal_is_not_authenticated_prune_or_delete_authority():
    from qa_managed_retention_policy import assess_passive_retention
    sample = actual_ord_samples()
    later = replace(sample[2], utc="2026-10-30T02:22:55Z",
                    recycle_kib=sample[2].recycle_kib - 1000,
                    used_kib=sample[2].used_kib - 1000,
                    avail_kib=sample[2].avail_kib + 1000)
    result = assess_passive_retention([sample[2], later], file_limit=1000,
                                      disk_limit_per=90)
    assert result["recycle_prune_corroborated"] is True
    assert result["prune_causality_verified"] is False
    assert result["execution_authorized"] is False


def test_real_board_rotation_proof_alone_does_not_authorize_reclaim():
    from qa_managed_retention_policy import assess_managed_retention
    proof = replace(verified(), automatic_root_rotation_observed=True,
                    automatic_recycle_prune_observed=None)
    result = assess_managed_retention(latest_actual_snapshot(), proof)
    assert result["current_used_percent"] == pytest.approx(89.47, abs=0.01)
    assert result["root_rotations_required"] == 344
    assert result["recycle_reclaim_kib_required"] > 0
    assert result["root_rotation_required"] is True
    assert result["recycle_prune_required"] is True
    assert "ORD_PRUNE_UNVERIFIED" in result["reason_codes"]
    assert "ORD_ROTATION_UNVERIFIED" not in result["reason_codes"]
    assert result["decision"] == "BLOCK"
    assert result["execution_authorized"] is False


def test_legacy_combined_cleanup_flag_cannot_verify_both_stages():
    from qa_managed_retention_policy import assess_managed_retention
    proof = replace(verified(), automatic_root_rotation_observed=None,
                    automatic_recycle_prune_observed=None,
                    automatic_rotation_and_prune_observed=True)
    result = assess_managed_retention(measured(), proof)
    assert "ORD_ROTATION_UNVERIFIED" in result["reason_codes"]
    assert "ORD_PRUNE_UNVERIFIED" in result["reason_codes"]
    assert result["decision"] == "BLOCK"


def test_no_cleanup_needed_does_not_require_unrelated_prune_proof():
    from qa_managed_retention_policy import assess_managed_retention
    low_usage = replace(measured(), used_kib=150_000_000,
                        avail_kib=80_000_000, root_files=200)
    proof = replace(verified(), automatic_root_rotation_observed=None,
                    automatic_recycle_prune_observed=None,
                    eligible_closed_root_files=0,
                    eligible_recycle_reclaim_kib=0)
    result = assess_managed_retention(low_usage, proof)
    assert result["root_rotations_required"] == 0
    assert result["recycle_reclaim_kib_required"] == 0
    assert result["root_rotation_required"] is False
    assert result["recycle_prune_required"] is False
    assert "ORD_ROTATION_UNVERIFIED" not in result["reason_codes"]
    assert "ORD_PRUNE_UNVERIFIED" not in result["reason_codes"]
    assert result["decision"] == "REVIEW_REQUIRED"
    assert result["execution_authorized"] is False


def test_passive_inference_rejects_mount_capacity_change():
    from qa_managed_retention_policy import assess_passive_retention
    rows = actual_ord_samples()
    # Different total capacity implies a changed filesystem/mount or source.
    changed = replace(rows[-1], avail_kib=rows[-1].avail_kib + 800_000)
    with pytest.raises(ValueError, match="capacity"):
        assess_passive_retention(rows[:-1] + [changed],
                                 file_limit=1000, disk_limit_per=90)


def test_passive_rotation_counts_are_net_changes_not_exact_move_counts():
    from qa_managed_retention_policy import assess_passive_retention
    result = assess_passive_retention(actual_ord_samples(),
                                      file_limit=1000, disk_limit_per=90)
    assert result["net_root_file_drop"] == 118
    assert result["net_mp4_file_drop"] == 59
    assert result["net_srt_file_drop"] == 59
    assert result["exact_move_count_verified"] is False
    assert result["four_channel_throughput_attested"] is False


def test_unmatched_other_file_decreases_cannot_prove_mp4_srt_rotation():
    from qa_managed_retention_policy import assess_passive_retention
    rows = actual_ord_samples()
    changed = replace(rows[-1], root_files=rows[-1].root_files + 1)
    result = assess_passive_retention(rows[:-1] + [changed],
                                      file_limit=1000, disk_limit_per=90)
    assert result["root_rotation_corroborated"] is False
