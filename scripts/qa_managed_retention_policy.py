"""Evidence-only assessment for ORD automatic SD retention. Never contacts a board."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qa_acceptance_safety import (
    EVENT_FILE_LIMIT, MIN_FILE_RESERVE, PLAN_SECONDS, RECORD_CHANNELS,
    RECORD_KBPS, Snapshot,
)

MIN_RESERVE_KIB = 512 * 1024
MAX_OBSERVATION_AGE_SECONDS = 120
# Include video plus matching SRT; unknown SRT disablement uses conservative two.
ROOT_FILES_PER_SEGMENT = 2


@dataclass(frozen=True)
class ManagedRetentionEvidence:
    installed_retention_source_attested: bool | None = None
    merged_runtime_attested: bool | None = None
    exclusive_board_lease_attested: bool | None = None
    retention_cleanup_scope_verified: bool | None = None
    recording_writer_lifecycle_verified: bool | None = None
    fd_visibility_verified: bool | None = None
    automatic_rotation_and_prune_observed: bool | None = None
    storage_mount_stable_verified: bool | None = None
    deletion_scope_authorized_for_this_campaign: bool | None = None
    full_campaign_restore_runner_verified: bool | None = None
    observation_age_seconds: int | None = None
    eligible_closed_root_files: int | None = None
    eligible_recycle_reclaim_kib: int | None = None
    cleanup_period_seconds: int | None = None
    max_rotated_files_per_pass: int | None = None


def _int(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def assess_managed_retention(
    snapshot: Snapshot,
    proof: ManagedRetentionEvidence,
    *,
    expected_duration_seconds: int = PLAN_SECONDS,
    projected_recording_kib: int | None = None,
    minimum_reserve_kib: int = MIN_RESERVE_KIB,
) -> dict[str, object]:
    """Only qualify complete evidence for review, never authorize execution.

    Unlike the static no-delete acceptance gate, model verified ORD recycling
    and eligible closed-session rotation. The raw recycle directory size is
    not proof of usable deletion capacity.
    """
    if type(snapshot) is not Snapshot or type(proof) is not ManagedRetentionEvidence:
        raise ValueError("invalid evidence/source")
    if not _int(expected_duration_seconds, 1) or not _int(minimum_reserve_kib, 1):
        raise ValueError("invalid duration/reserve")
    projected = (int(RECORD_KBPS * 1000 / 8 * expected_duration_seconds / 1024)
                 if projected_recording_kib is None else projected_recording_kib)
    if not _int(projected, 1):
        raise ValueError("invalid projected bytes")
    for field in (
        "used_kib", "avail_kib", "root_files", "event_files", "event_kib",
        "recycle_kib", "recovery_files",
    ):
        if not _int(getattr(snapshot, field)):
            raise ValueError("invalid observation: " + field)
    for field in ("disk_limit_per", "disk_limit_file", "event_storage_size"):
        if not _int(getattr(snapshot, field), 1):
            raise ValueError("invalid policy: " + field)
    if snapshot.disk_limit_per > 100 or snapshot.event_storage_size > 100:
        raise ValueError("invalid percentage")
    for field in ("disk_manage", "event_auto_remove", "stale_restart_markers"):
        if type(getattr(snapshot, field)) is not bool:
            raise ValueError("invalid flag: " + field)
    capacity = snapshot.used_kib + snapshot.avail_kib
    if capacity <= 0:
        raise ValueError("invalid capacity")

    proofs = (
        ("installed_retention_source_attested", "INSTALLED_RETENTION_UNVERIFIED"),
        ("merged_runtime_attested", "RUNTIME_POLICY_UNVERIFIED"),
        ("exclusive_board_lease_attested", "EXCLUSIVE_LEASE_UNVERIFIED"),
        ("retention_cleanup_scope_verified", "CLEANUP_SCOPE_UNVERIFIED"),
        ("recording_writer_lifecycle_verified", "WRITER_LIFECYCLE_UNVERIFIED"),
        ("fd_visibility_verified", "FD_VISIBILITY_UNVERIFIED"),
        ("automatic_rotation_and_prune_observed", "ORD_CLEANUP_BEHAVIOR_UNVERIFIED"),
        ("storage_mount_stable_verified", "SD_MOUNT_UNVERIFIED"),
        ("deletion_scope_authorized_for_this_campaign", "CLEANUP_NOT_AUTHORIZED"),
        ("full_campaign_restore_runner_verified", "DURABLE_RESTORE_RUNNER_UNVERIFIED"),
    )
    blockers = [code for name, code in proofs if getattr(proof, name) is not True]
    if not snapshot.disk_manage:
        blockers.append("DISK_MANAGEMENT_DISABLED")
    if snapshot.recovery_files or snapshot.stale_restart_markers:
        blockers.append("RECOVERY_JOURNAL_UNCLEAN")
    if snapshot.event_files + MIN_FILE_RESERVE >= EVENT_FILE_LIMIT:
        blockers.append("EVENT_FILE_COUNT_NEAR_LIMIT")
    if snapshot.event_kib >= capacity * snapshot.event_storage_size // 100:
        blockers.append("EVENT_SPACE_AT_QUOTA")
    if not _int(proof.observation_age_seconds) or (
        proof.observation_age_seconds > MAX_OBSERVATION_AGE_SECONDS
    ):
        blockers.append("OBSERVATION_NOT_FRESH")

    projected_files = (RECORD_CHANNELS * ((expected_duration_seconds + 59) // 60)
                       * ROOT_FILES_PER_SEGMENT)
    required_rotations = max(
        0, snapshot.root_files + projected_files + MIN_FILE_RESERVE
        - snapshot.disk_limit_file
    )
    required_recycle_kib = max(
        0, snapshot.used_kib + projected + minimum_reserve_kib
        - (capacity * snapshot.disk_limit_per // 100)
    )

    eligible_root = proof.eligible_closed_root_files
    if not _int(eligible_root) or eligible_root > snapshot.root_files:
        blockers.append("ELIGIBLE_ROOT_ROTATION_UNVERIFIED")
    elif eligible_root < required_rotations:
        blockers.append("ROOT_ROTATION_CAPACITY_INSUFFICIENT")

    eligible_recycle = proof.eligible_recycle_reclaim_kib
    if not _int(eligible_recycle) or eligible_recycle > snapshot.recycle_kib:
        blockers.append("RECLAIMABLE_RECYCLE_UNVERIFIED")
    elif eligible_recycle < required_recycle_kib:
        blockers.append("RECLAIMABLE_RECYCLE_INSUFFICIENT")

    if not _int(proof.cleanup_period_seconds, 1) or not _int(proof.max_rotated_files_per_pass, 1):
        blockers.append("RETENTION_THROUGHPUT_UNVERIFIED")
        possible_rotations = None
    else:
        possible_rotations = (
            expected_duration_seconds // proof.cleanup_period_seconds
        ) * proof.max_rotated_files_per_pass
        if possible_rotations < required_rotations:
            blockers.append("RETENTION_THROUGHPUT_INSUFFICIENT")

    if (snapshot.avail_kib +
            (eligible_recycle if _int(eligible_recycle) else 0)
            <= projected + minimum_reserve_kib):
        blockers.append("PHYSICAL_SPACE_INSUFFICIENT")

    return {
        "decision": "REVIEW_REQUIRED" if not blockers else "BLOCK",
        "execution_authorized": False,
        "reason_codes": tuple(dict.fromkeys(blockers)),
        "current_used_percent": round(100 * snapshot.used_kib / capacity, 2),
        "projected_recording_kib": projected,
        "projected_file_count": projected_files,
        "root_rotations_required": required_rotations,
        "recycle_reclaim_kib_required": required_recycle_kib,
        "root_rotations_possible_by_period": possible_rotations,
        "evidence_only": True,
    }
