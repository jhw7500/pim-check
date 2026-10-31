"""Conservative offline storage estimate for the no-MCP four-channel QA case.

No hardware, SSH, network, lease, file removal or runtime execution API.
A capacity-only PASS is deliberately NOT permission to operate the board.
"""
from __future__ import annotations

from math import ceil

from scripts.qa_acceptance_safety import Snapshot

TARGET = "192.168.214.4"
CHANNELS = (("i2c2", "ch0"), ("i2c2", "ch1"),
            ("i2c1", "ch2"), ("i2c1", "ch3"))
DEFAULT_RESERVE_KIB = 512 * 1024
DEFAULT_FILE_RESERVE = 16
DEFAULT_RESTORE_WINDOW_SECONDS = 300
MAX_OBSERVATION_AGE_SECONDS = 120


class AdmissionError(ValueError):
    """Malformed or unverified observation/profile: fail closed."""


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AdmissionError("invalid " + field)
    return value


def _profile_contract(profile: object) -> tuple[int, int, int, int]:
    if type(profile) is not dict:
        raise AdmissionError("profile is not a mapping")
    try:
        if profile["target"]["host"] != TARGET:
            raise AdmissionError("profile target mismatch")
        if "mcp_absent" not in profile["tags"]:
            raise AdmissionError("missing absent-hardware profile tag")
        setup = profile["setup"]
        if setup.get("reboot_after") is not True:
            raise AdmissionError("reboot/restore contract missing")
        if setup.get("inject_command") or setup.get("recovery_command"):
            raise AdmissionError("fault injection or custom recovery is excluded")
        if setup.get("ord_vcm_changes") != {".VCM.srt_enable": True}:
            raise AdmissionError("unexpected ORD/VCM change")
        changes = setup["edgeconf_changes"]
        for bus, chan in CHANNELS:
            prefix = f".VHL_CAM.{bus}.{chan}"
            if changes[prefix + ".enable"] is not True:
                raise AdmissionError("not all four channels enabled")
            if changes[prefix + ".led_flash.enable"] is not False:
                raise AdmissionError("no-MCP LED must remain disabled")
        if profile["checks"]["frame_flow"]["enabled"] is not True:
            raise AdmissionError("frame flow verification is not enabled")
        if profile["checks"]["recording"]["session_progress"] is None:
            raise AdmissionError("finalized recording proof not required")
        if profile["checks"]["recording"]["expected_channels"] != 4:
            raise AdmissionError("recording channel count mismatch")
        record_min = _integer(changes[".VHL_CAM.recording_time"],
                              "recording_time", minimum=1)
        rates = []
        for bus, chan in CHANNELS:
            bitrate = changes[f".VHL_CAM.{bus}.{chan}.bps"]
            if type(bitrate) is not list or len(bitrate) != 2:
                raise AdmissionError("invalid channel bitrate pair")
            _integer(bitrate[0], "initial channel bitrate")
            rates.append(_integer(bitrate[1], "channel bitrate", minimum=1))
        stabilize = _integer(setup.get("stabilize_sec", 0),
                             "setup stabilization seconds")
        monitor = _integer(profile["monitor"]["duration_sec"],
                           "monitor seconds", minimum=1)
        return sum(rates), stabilize, monitor, record_min
    except (KeyError, TypeError, IndexError) as exc:
        raise AdmissionError("incomplete 4ch profile") from exc


def assess_bounded_4ch(
    snapshot: Snapshot,
    profile: dict,
    *,
    age_seconds: int,
    restore_window_seconds: int = DEFAULT_RESTORE_WINDOW_SECONDS,
    reserve_kib: int = DEFAULT_RESERVE_KIB,
    file_reserve: int = DEFAULT_FILE_RESERVE,
    max_extra_segments_per_channel: int | None = None,
) -> dict[str, object]:
    """Calculate *upper-bound* space needs without claiming cleanup evidence.

    Start/monitor/restore are all counted as potential recording intervals.
    MP4+SRT are budgeted as TWO root entries per channel/segment, which
    intentionally overestimates one shared SRT per four-channel session.

    Each interruption/restart can create a partial additional MP4/SRT segment
    per channel. A missing bound blocks even an offline capacity review.
    Explicit bounds are planning assumptions, NOT evidence of restart control.
    The model does not see actual writer EOS, eligible recycle entries,
    board lease fencing, or installed Finalizer. It can NEVER authorize
    hardware mutation. Freshness must be independently attested by the owner.
    """
    if type(snapshot) is not Snapshot:
        raise AdmissionError("unverified snapshot type")
    age = _integer(age_seconds, "observation age")
    if age > MAX_OBSERVATION_AGE_SECONDS:
        raise AdmissionError("snapshot is stale")
    restore = _integer(restore_window_seconds, "restore window", minimum=1)
    reserve = _integer(reserve_kib, "space reserve", minimum=1)
    keep_files = _integer(file_reserve, "file reserve", minimum=1)
    additional_segments = None
    if max_extra_segments_per_channel is not None:
        additional_segments = _integer(
            max_extra_segments_per_channel, "extra segment bound")

    rate, stabilize, monitor, record_min = _profile_contract(profile)
    numbers = ("used_kib", "avail_kib", "root_files", "event_files",
               "event_kib", "recycle_kib", "recovery_files")
    for field in numbers:
        _integer(getattr(snapshot, field), field)
    for field in ("disk_limit_per", "disk_limit_file", "event_storage_size"):
        _integer(getattr(snapshot, field), field, minimum=1)
    if snapshot.disk_limit_per > 100 or snapshot.event_storage_size > 100:
        raise AdmissionError("invalid configured percentage")
    for name in ("disk_manage", "event_auto_remove", "stale_restart_markers"):
        if type(getattr(snapshot, name)) is not bool:
            raise AdmissionError("invalid storage policy flag")
    capacity = snapshot.used_kib + snapshot.avail_kib
    if capacity <= 0:
        raise AdmissionError("invalid filesystem capacity")

    active_seconds = stabilize + monitor + restore
    # Ceil conservatively rather than truncate video bytes.
    video_kib = ceil(rate * 1000 * active_seconds / 8 / 1024)
    # Allow two root files per channel per configured recording segment.
    # This is deliberately conservative even if one SRT is shared.
    segment_seconds = record_min * 60
    session_count = ceil(active_seconds / segment_seconds)
    # Each interrupted session can finalize additional partial MP4/SRT pairs.
    # An unbounded owner restart must never silently count as zero.
    budgeted_sessions = session_count + (additional_segments or 0)
    projected_files = len(CHANNELS) * 2 * budgeted_sessions
    limit_bytes = (capacity * snapshot.disk_limit_per) // 100
    slack = max(0, limit_bytes - snapshot.used_kib)
    projected_root = snapshot.root_files + projected_files
    needed_bytes = max(0, video_kib + reserve - slack + 1)
    required_rotations = max(
        0, projected_root + keep_files - snapshot.disk_limit_file + 1)
    reasons: list[str] = []
    if additional_segments is None:
        reasons.append("EXTRA_SEGMENT_BOUND_MISSING")
    if not snapshot.disk_manage:
        reasons.append("DISK_MANAGEMENT_DISABLED")
    if snapshot.recovery_files:
        reasons.append("DIRTY_RECOVERY_JOURNAL")
    if snapshot.stale_restart_markers:
        reasons.append("STALE_RESTART_MARKERS")
    if video_kib + reserve >= slack:
        reasons.append("INSUFFICIENT_SD_HEADROOM_WITH_RESERVE")
    if projected_root + keep_files >= snapshot.disk_limit_file:
        reasons.append("ROOT_FILE_CAPACITY_WITH_RESERVE")
    if video_kib + reserve >= snapshot.avail_kib:
        reasons.append("INSUFFICIENT_PHYSICAL_SD_BYTES")

    return {
        "schema": 1,
        "target": TARGET,
        "decision": "BLOCK" if reasons else "CAPACITY_REVIEW_ONLY",
        "reason_codes": reasons,
        "board_touched": False,
        "execution_authorized": False,
        "evidence_only": True,
        "real_restore_performed": False,
        "source_and_binary_identity_verified": False,
        "writer_eos_or_fsync_verified": False,
        "prune_causality_verified": False,
        "recording_kbps": rate,
        "setup_stabilize_seconds": stabilize,
        "monitor_seconds": monitor,
        "restore_window_seconds": restore,
        "writer_exposure_seconds": active_seconds,
        "recording_segment_seconds": segment_seconds,
        "nominal_recording_segments_per_channel": session_count,
        "max_extra_segments_per_channel": additional_segments,
        "budgeted_segments_per_channel": budgeted_sessions,
        "extra_segment_budget_is_planning_assumption": True,
        "projected_recording_kib": video_kib,
        "projected_file_count": projected_files,
        "root_files_after_projection": projected_root,
        "root_file_limit": snapshot.disk_limit_file,
        "headroom_to_limit_kib": slack,
        "minimum_unused_capacity_kib": reserve,
        "minimum_unused_file_slots": keep_files,
        "required_reclaim_kib": needed_bytes,
        "required_root_rotations": required_rotations,
        "recycle_kib_observed_not_reclaimable_proof": snapshot.recycle_kib,
    }
