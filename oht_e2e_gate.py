"""Offline, fail-closed readiness assessment before an OHT COPY E2E run.

This module sends no OHT packets, acquires no board lease, and never deletes
recordings. Evidence supplied by a caller is NOT a substitute for an actual
owner-controlled exclusive lease or independently attested installed binaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class CopyPreflight:
    # External attestations are initially unknown; set to True only after
    # independently verifying source-to-installed-binary and merged runtime.
    source_contract_verified: bool | None = None
    exclusive_lease_verified: bool | None = None
    merged_runtime_verified: bool | None = None

    machine_id_length: int | None = None
    ord_port: int | None = None
    vcm_port: int | None = None
    recording_minutes: int | None = None
    srt_enabled: bool | None = None
    target_copy: bool | None = None
    event_auto_remove: bool | None = None
    # The caller must opt in for this individual E2E session; never default
    # to permission to delete older recordings.
    allow_existing_recording_deletion: bool | None = None
    # Independent source/config evidence must confirm the automatic cleanup
    # cannot prune unrelated files (rather than trusting the consent alone).
    automatic_cleanup_scope_verified: bool | None = None
    disk_management_enabled: bool | None = None
    # A filename age threshold alone cannot prove that the recorder closed
    # its write FD. Require an independently verified writer/finalizer
    # lifecycle contract whenever automatic retention may delete files.
    recording_writer_lifecycle_verified: bool | None = None

    disk_used_percent: int | None = None
    disk_limit_percent: int | None = None
    minimum_headroom_percent: int | None = None
    available_kib: int | None = None
    expected_new_kib: int | None = None
    minimum_reserve_kib: int | None = None
    recovery_journal_files: int | None = None


def _integer(value: object, minimum: int, maximum: int | None = None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= minimum and (maximum is None or value <= maximum))


def assess_copy_preflight(observation: CopyPreflight) -> tuple[str, ...]:
    """Return stable blocker codes; an empty tuple is necessary, not sufficient.

    Even a passing metadata assessment does not authorize an actual packet
    sender. The caller must validate a real active lease and run a bounded
    collector with independently verified source/installed-binary identity.
    """
    blockers = []
    for field, code in (
        ("source_contract_verified", "SOURCE_BINDING_UNVERIFIED"),
        ("exclusive_lease_verified", "EXCLUSIVE_LEASE_UNVERIFIED"),
        ("merged_runtime_verified", "ACTIVE_RUNTIME_UNVERIFIED"),
        ("srt_enabled", "SRT_NOT_ENABLED"),
        ("target_copy", "COPY_NOT_ENABLED"),
    ):
        if getattr(observation, field) is not True:
            blockers.append(code)

    if not _integer(observation.machine_id_length, 6, 6):
        blockers.append("MACHINE_ID_INVALID")
    for field, code in (
        ("ord_port", "ORD_PORT_INVALID"),
        ("vcm_port", "VCM_PORT_INVALID"),
    ):
        if not _integer(getattr(observation, field), 1, 65535):
            blockers.append(code)
    if not _integer(observation.recording_minutes, 1):
        blockers.append("RECORDING_DURATION_UNVERIFIED")
    if not _integer(observation.recovery_journal_files, 0) or observation.recovery_journal_files != 0:
        blockers.append("RECOVERY_JOURNAL_NOT_CLEAN")

    # The user's deletion permission applies to existing recordings ONLY,
    # never to arbitrary board files. An actual run must also verify the
    # specific ORD cleanup behavior and its configured scope.
    # Normal recording retention and event-copy retention are independent.
    # event_auto_remove=False does NOT disable automatic SD recording cleanup.
    event_cleanup = observation.event_auto_remove is True
    recording_cleanup = observation.disk_management_enabled is True
    deletion_possible = event_cleanup or recording_cleanup
    deletion_authorized = observation.allow_existing_recording_deletion is True
    scope_verified = observation.automatic_cleanup_scope_verified is True
    writer_verified = observation.recording_writer_lifecycle_verified is True
    cleanup_verified = (
        deletion_possible and deletion_authorized
        and scope_verified and recording_cleanup and writer_verified
    )
    if observation.event_auto_remove is not True and observation.event_auto_remove is not False:
        blockers.append("AUTO_DELETE_POLICY_UNVERIFIED")
    if observation.disk_management_enabled is not True and observation.disk_management_enabled is not False:
        blockers.append("DISK_MANAGEMENT_UNVERIFIED")
    # Unknown cleanup toggles are treated as potentially active until proven disabled.
    if (observation.event_auto_remove is not False
            or observation.disk_management_enabled is not False) and not deletion_authorized:
        blockers.append("AUTO_DELETE_NOT_DISABLED")
    if deletion_possible and not writer_verified:
        blockers.append("RECORDING_WRITER_LIFECYCLE_UNVERIFIED")
    if deletion_possible and deletion_authorized:
        if not scope_verified:
            blockers.append("AUTOMATIC_CLEANUP_SCOPE_UNVERIFIED")
        # The event policy cannot prove the actual disk manager is active.
        if event_cleanup and observation.disk_management_enabled is not True:
            if "DISK_MANAGEMENT_UNVERIFIED" not in blockers:
                blockers.append("DISK_MANAGEMENT_UNVERIFIED")

    used, limit = observation.disk_used_percent, observation.disk_limit_percent
    if not _integer(used, 0, 100) or not _integer(limit, 1, 100):
        blockers.append("DISK_POLICY_INVALID")
    elif used >= limit and not cleanup_verified:
        blockers.append("DISK_LIMIT_REACHED")

    # Below the management threshold, reserve percentage headroom as before.
    # With explicitly authorized, verified ORD pruning, the hard percentage
    # limit is replaced by the independently measured byte-space budget below.
    margin = observation.minimum_headroom_percent
    if not cleanup_verified:
        if not _integer(margin, 1, 99):
            blockers.append("HEADROOM_POLICY_UNVERIFIED")
        elif _integer(used, 0, 100) and _integer(limit, 1, 100) and used + margin >= limit:
            blockers.append("DISK_HEADROOM_INSUFFICIENT")

    free, projected, reserve = (
        observation.available_kib, observation.expected_new_kib,
        observation.minimum_reserve_kib,
    )
    if not all(_integer(value, 1) for value in (free, projected, reserve)):
        blockers.append("SPACE_BUDGET_UNVERIFIED")
    elif free <= projected + reserve:
        blockers.append("SPACE_BUDGET_EXHAUSTED")

    return tuple(blockers)


def from_observation(data: Mapping[str, object]) -> CopyPreflight:
    """Reject unknown keys; absent fields stay unknown and fail closed."""
    fields = CopyPreflight.__dataclass_fields__
    unknown = set(data) - set(fields)
    if unknown:
        raise ValueError(f"unknown copy preflight keys: {sorted(unknown)}")
    return CopyPreflight(**data)
