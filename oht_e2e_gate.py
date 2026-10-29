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

    used, limit = observation.disk_used_percent, observation.disk_limit_percent
    if not _integer(used, 0, 100) or not _integer(limit, 1, 100):
        blockers.append("DISK_POLICY_INVALID")
    elif used >= limit:
        blockers.append("DISK_LIMIT_REACHED")

    margin = observation.minimum_headroom_percent
    if not _integer(margin, 1, 99):
        blockers.append("HEADROOM_POLICY_UNVERIFIED")
    elif _integer(used, 0, 100) and _integer(limit, 1, 100) and used + margin >= limit:
        blockers.append("DISK_HEADROOM_INSUFFICIENT")

    # COPY can invoke ORD's disk management; do not implicitly approve
    # a run that might prune unrelated recordings.
    if observation.event_auto_remove is not False:
        blockers.append("AUTO_DELETE_NOT_DISABLED")

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
