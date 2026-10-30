"""Evidence-only assessment for ORD automatic SD retention. Never contacts a board."""
from __future__ import annotations

from dataclasses import dataclass
import re
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
    # Legacy combined statement cannot attest independent rotation and prune.
    automatic_rotation_and_prune_observed: bool | None = None
    automatic_root_rotation_observed: bool | None = None
    automatic_recycle_prune_observed: bool | None = None
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

    # ORD's file-count threshold and its disk-use prune threshold are
    # independent. Check each stage only when the projected campaign needs it.
    if required_rotations and proof.automatic_root_rotation_observed is not True:
        blockers.append("ORD_ROTATION_UNVERIFIED")
    if required_recycle_kib and proof.automatic_recycle_prune_observed is not True:
        blockers.append("ORD_PRUNE_UNVERIFIED")

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
        "root_rotation_required": required_rotations > 0,
        "recycle_prune_required": required_recycle_kib > 0,
        "root_rotations_possible_by_period": possible_rotations,
        "evidence_only": True,
    }


@dataclass(frozen=True)
class PassiveRetentionSample:
    """One passive counter observation; not a verified ORD cleanup transaction."""

    utc: str
    root_files: int
    mp4_files: int
    srt_files: int
    recycle_dirs: int
    recycle_kib: int
    used_kib: int
    avail_kib: int


def assess_passive_retention(
    samples: list[PassiveRetentionSample] | tuple[PassiveRetentionSample, ...],
    *,
    file_limit: int,
    disk_limit_per: int,
) -> dict[str, object]:
    """Correlate bounded read-only SD counters; never approve a destructive act.

    A decrease of MP4/SRT counts paired with growing recycle usage corroborates
    rotation, not the safety of an individual move. A recycle shrink paired
    with increasing free space suggests reclaim, not verified prune causality.
    Only trusted, independent source/writer/lease evidence can authorize QA.
    """
    from datetime import datetime, timezone

    if not isinstance(samples, (list, tuple)) or not 2 <= len(samples) <= 120:
        raise ValueError("invalid passive observation count")
    if not _int(file_limit, 1) or not _int(disk_limit_per, 1) or disk_limit_per > 100:
        raise ValueError("invalid installed retention policy")

    timestamps = []
    for row in samples:
        if type(row) is not PassiveRetentionSample or not isinstance(row.utc, str):
            raise ValueError("invalid passive observation type")
        try:
            ts = datetime.fromisoformat(row.utc.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid passive timestamp") from None
        if ts.tzinfo is None:
            raise ValueError("timezone missing from passive timestamp")
        timestamps.append(ts.astimezone(timezone.utc))
        for name in ("root_files", "mp4_files", "srt_files", "recycle_dirs",
                     "recycle_kib", "used_kib", "avail_kib"):
            if not _int(getattr(row, name)):
                raise ValueError("invalid passive count: " + name)
        if row.root_files < row.mp4_files + row.srt_files or row.used_kib + row.avail_kib <= 0:
            raise ValueError("inconsistent passive observation")

    # df used+avail is the effective ORD denominator on the same SD mount.
    # A changed denominator may be a remount/other source; no inference is safe.
    totals = [row.used_kib + row.avail_kib for row in samples]
    if any(total != totals[0] for total in totals[1:]):
        raise ValueError("passive filesystem capacity changed")

    if any(a >= b for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("out-of-order passive observation")

    rotation = None
    prune_signal = False
    for i in range(len(samples) - 1):
        before, after = samples[i], samples[i + 1]
        elapsed = (timestamps[i + 1] - timestamps[i]).total_seconds()
        # Large sampling gaps cannot attribute the observed counter change to
        # one automatic cleanup pass.
        if elapsed > 120:
            continue
        moved_mp4 = before.mp4_files - after.mp4_files
        moved_srt = before.srt_files - after.srt_files
        moved_root = before.root_files - after.root_files
        if (before.root_files > file_limit and moved_mp4 > 0
                and moved_mp4 == moved_srt
                and moved_root == moved_mp4 + moved_srt
                and after.recycle_kib > before.recycle_kib
                and after.recycle_dirs > before.recycle_dirs):
            candidate = (moved_root, moved_mp4, moved_srt,
                         after.recycle_kib - before.recycle_kib)
            if rotation is None or candidate[0] > rotation[0]:
                rotation = candidate
        if (before.recycle_kib > after.recycle_kib
                and after.avail_kib > before.avail_kib
                and after.used_kib < before.used_kib):
            prune_signal = True

    current = samples[-1]
    return {
        "root_rotation_corroborated": rotation is not None,
        "rotation_root_files": rotation[0] if rotation else 0,
        "rotation_mp4_files": rotation[1] if rotation else 0,
        "rotation_srt_files": rotation[2] if rotation else 0,
        "recycle_growth_kib": rotation[3] if rotation else 0,
        # Net count changes are NOT the exact number of move(2) operations;
        # new recordings may have been created during the sample interval.
        "net_root_file_drop": rotation[0] if rotation else 0,
        "net_mp4_file_drop": rotation[1] if rotation else 0,
        "net_srt_file_drop": rotation[2] if rotation else 0,
        "exact_move_count_verified": False,
        "four_channel_throughput_attested": False,
        "recycle_prune_corroborated": prune_signal,
        "prune_causality_verified": False,
        "effective_used_percent": round(
            100 * current.used_kib / (current.used_kib + current.avail_kib), 2),
        "installed_source_attested": False,
        "writer_lifecycle_attested": False,
        "execution_authorized": False,
        "evidence_only": True,
    }


@dataclass(frozen=True)
class PassiveWriterSample:
    """RAM .part, process FD and SD final filename snapshots; metadata only."""

    utc: str
    fd_video_parts: tuple[str, ...]
    ram_video_parts: tuple[str, ...]
    ram_srt_parts: tuple[str, ...]
    sd_video_finals: tuple[str, ...]
    sd_srt_finals: tuple[str, ...]


def assess_passive_writer_lifecycle(
    samples: list[PassiveWriterSample] | tuple[PassiveWriterSample, ...],
) -> dict[str, object]:
    """Correlate observed RAM-to-SD names; never attest EOS, fsync or 4ch.

    A vanished FD from only the scanned processes cannot independently
    establish writer closure. Neither a matching file name nor nonzero file
    size can establish bytes copied, fsync, or container finalization.
    """
    from datetime import datetime, timezone

    if (not isinstance(samples, (list, tuple)) or not 2 <= len(samples) <= 120):
        raise ValueError("invalid passive writer observation count")

    prefix = r"[A-Za-z0-9]{1,20}_[0-9]{8}_[0-9]{6}"
    video_part = re.compile(rf"({prefix})-ch([0-3])\.mp4\.part\Z")
    srt_part = re.compile(rf"{prefix}-data\.srt\.part\Z")
    video_final = re.compile(rf"{prefix}-ch[0-3]\.mp4\Z")
    srt_final = re.compile(rf"{prefix}-data\.srt\Z")
    types = {
        "fd_video_parts": video_part,
        "ram_video_parts": video_part,
        "ram_srt_parts": srt_part,
        "sd_video_finals": video_final,
        "sd_srt_finals": srt_final,
    }
    stamps = []
    for sample in samples:
        if type(sample) is not PassiveWriterSample or not isinstance(sample.utc, str):
            raise ValueError("invalid writer observation type")
        try:
            stamp = datetime.fromisoformat(sample.utc.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid writer timestamp") from None
        if stamp.tzinfo is None:
            raise ValueError("writer timestamp requires timezone")
        stamps.append(stamp.astimezone(timezone.utc))
        for key, validator in types.items():
            entries = getattr(sample, key)
            if (type(entries) is not tuple or len(entries) > 64
                    or len(set(entries)) != len(entries)):
                raise ValueError("invalid writer filename inventory")
            if any(not isinstance(name, str) or validator.fullmatch(name) is None
                   for name in entries):
                raise ValueError("untrusted writer filename")

    if any(a >= b for a, b in zip(stamps, stamps[1:])):
        raise ValueError("out of order writer observations")

    seen: set[tuple[str, str]] = set()
    sessions: list[str] = []
    channels: set[str] = set()
    for i, (before, after) in enumerate(zip(samples, samples[1:])):
        if (stamps[i + 1] - stamps[i]).total_seconds() > 120:
            continue
        for name in before.fd_video_parts:
            assert (match := video_part.fullmatch(name)) is not None
            session, chan = match.groups()
            if name not in before.ram_video_parts:
                continue
            if session + "-data.srt.part" not in before.ram_srt_parts:
                continue
            if (name in after.fd_video_parts or name in after.ram_video_parts
                    or session + "-data.srt.part" in after.ram_srt_parts):
                continue
            if (session + f"-ch{chan}.mp4" not in after.sd_video_finals
                    or session + "-data.srt" not in after.sd_srt_finals):
                continue
            key = session, chan
            if key in seen:
                continue
            seen.add(key)
            sessions.append(session)
            channels.add("ch" + chan)

    return {
        "correlated_mp4_srt_pairs": len(seen),
        "correlated_session_ids": sessions,
        "observed_channels": sorted(channels),
        "writer_eos_attested": False,
        "fsync_attested": False,
        "file_bytes_integrity_attested": False,
        "four_channel_throughput_attested": False,
        "execution_authorized": False,
        "evidence_only": True,
    }
