"""Offline validation of a *direct-copy* ORD/VCM E2E evidence bundle.

No socket, SSH, filesystem, board lease, deletion, or recorder is opened here.
This validator only checks consistency of independently collected observations.
It cannot turn synthetic metadata into a hardware acceptance claim.

The ORD tail/merge COPY branch needs its own provenance contract: this strict
direct-copy validator intentionally rejects concatenated or transformed output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
import re


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SESSION = re.compile(r"^[0-9]{8}_[0-9]{4}$")
_VIDEO_SUFFIXES = {".mp4", ".ts", ".mkv"}
# A valid hash/size alone cannot make a file from another recording minute or
# camera channel part of this COPY result. Seconds of the first video fragment
# may be nonzero; SRT is minute-aligned by the product naming contract.
_VIDEO_NAME = re.compile(
    r"^.+_(?P<session>[0-9]{8}_[0-9]{4})(?P<seconds>[0-5][0-9])-"
    r"ch(?P<channel>[0-3])\.(?:mp4|ts|mkv)$"
)
_SRT_NAME = re.compile(
    r"^.+_(?P<session>[0-9]{8}_[0-9]{4})00-data\.srt$"
)
# ORD waitingCopy uses sprintf("_rmsMax_%0.1f", rmsMax) on event
# destinations only. Its scoped retention parser permits up to ten integer
# digits and an optional minus before a single fractional digit. An event's
# video and SRT must carry exactly the same tag, or neither carry one.
_EVENT_RMS_SUFFIX = r"(?:_rmsMax_(?P<rms>-?[0-9]{1,10}\.[0-9]))?"
_EVENT_VIDEO_NAME = re.compile(
    r"^.+_(?P<session>[0-9]{8}_[0-9]{4})(?P<seconds>[0-5][0-9])"
    + _EVENT_RMS_SUFFIX
    + r"-ch(?P<channel>[0-3])\.(?:mp4|ts|mkv)$"
)
_EVENT_SRT_NAME = re.compile(
    r"^.+_(?P<session>[0-9]{8}_[0-9]{4})00"
    + _EVENT_RMS_SUFFIX
    + r"-data\.srt$"
)
_ROLES = {"recorded_video", "recorded_srt", "event_video", "event_srt"}


def _valid_session(value: object) -> bool:
    if not isinstance(value, str) or not _SESSION.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y%m%d_%H%M")
    except ValueError:
        return False
    return True


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _safe_path(path: object) -> bool:
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    p = PurePosixPath(path)
    return p.is_absolute() and ".." not in p.parts and path == str(p)


def _is_contained(path: str, root: str) -> bool:
    return PurePosixPath(path).is_relative_to(PurePosixPath(root))


@dataclass(frozen=True)
class FinalArtifact:
    role: str
    path: str
    session: str
    boot_id: str
    channel: int | None
    sha256: str
    size_bytes: int
    mtime_ns: int
    finalization_verified: bool | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class DirectCopyEvidence:
    # Both directory roots and the before-event listing must come from one
    # inspected board and be explicitly supplied; no implicit defaults.
    recording_root: str | None = None
    event_root: str | None = None
    boot_id: str | None = None
    session: str | None = None
    enabled_channels: tuple[int, ...] | None = None

    anchor_ns: int | None = None
    completed_ns: int | None = None
    attempted_requests: int | None = None
    request_frame_verified: bool | None = None
    response_frame_verified: bool | None = None
    request_frame_sha256: str | None = None
    response_frame_sha256: str | None = None
    request_command: str | None = None
    expected_status_token: str | None = None
    vcm_status_token: str | None = None
    srt_status_token: str | None = None
    srt_content_verified: bool | None = None

    # An empty tuple is meaningful: the collector observed no event files.
    # None means no baseline was taken and must never be treated as empty.
    preexisting_event_paths: tuple[str, ...] | None = None
    preflight_blockers: tuple[str, ...] | None = None
    artifacts: tuple[FinalArtifact, ...] = ()


def evaluate_direct_copy_evidence(bundle: DirectCopyEvidence) -> tuple[str, ...]:
    """Return fail-closed finding codes for a *direct-copy* proof bundle.

    A clean result means only that evidence is internally consistent. Board
    identity, source attestation, direct-copy semantics and artifact bytes must
    independently be measured and checked by a trusted collector.
    """
    issues: list[str] = []

    if bundle.preflight_blockers is None:
        issues.append("PREFLIGHT_EVIDENCE_MISSING")
    elif not isinstance(bundle.preflight_blockers, tuple) or bundle.preflight_blockers:
        issues.append("PREFLIGHT_BLOCKED")

    if not isinstance(bundle.boot_id, str) or not bundle.boot_id.strip():
        issues.append("BOOT_ID_MISSING")
    if not _valid_session(bundle.session):
        issues.append("SESSION_INVALID")
    channels = bundle.enabled_channels
    if (not isinstance(channels, tuple) or not channels
            or any(not isinstance(ch, int) or isinstance(ch, bool)
                   or ch < 0 or ch > 3 for ch in channels)
            or len(set(channels)) != len(channels)):
        issues.append("CHANNEL_POLICY_INVALID")
        channels = ()

    roots = (bundle.recording_root, bundle.event_root)
    if not all(_safe_path(root) for root in roots):
        issues.append("ARTIFACT_ROOTS_UNVERIFIED")
    elif (bundle.recording_root == bundle.event_root
          or not _is_contained(bundle.event_root, bundle.recording_root)):
        issues.append("ARTIFACT_ROOTS_UNVERIFIED")

    if not _positive_int(bundle.anchor_ns) or not _positive_int(bundle.completed_ns):
        issues.append("EVENT_TIMELINE_INVALID")
    elif bundle.completed_ns <= bundle.anchor_ns:
        issues.append("EVENT_TIMELINE_INVALID")

    if bundle.attempted_requests != 1 or isinstance(bundle.attempted_requests, bool):
        issues.append("REQUEST_COUNT_NOT_ONE")
    if (bundle.request_frame_verified is not True
            or bundle.response_frame_verified is not True
            or bundle.request_command != "COPY"
            or not isinstance(bundle.request_frame_sha256, str)
            or not _SHA256.fullmatch(bundle.request_frame_sha256)
            or not isinstance(bundle.response_frame_sha256, str)
            or not _SHA256.fullmatch(bundle.response_frame_sha256)):
        issues.append("OHT_FRAME_UNVERIFIED")

    token = bundle.expected_status_token
    if (not isinstance(token, str) or len(token.strip()) < 4
            or bundle.vcm_status_token != token or bundle.srt_status_token != token
            or bundle.srt_content_verified is not True):
        issues.append("VCM_SRT_CORRELATION_UNVERIFIED")

    previous = bundle.preexisting_event_paths
    if (not isinstance(previous, tuple)
            or any(not _safe_path(path) for path in previous)):
        issues.append("EVENT_BASELINE_MISSING")
        previous = ()
    if len(set(previous)) != len(previous):
        issues.append("EVENT_BASELINE_DUPLICATE")
    if (_safe_path(bundle.event_root)
            and any(not _is_contained(path, bundle.event_root) for path in previous)):
        issues.append("EVENT_BASELINE_SCOPE_INVALID")

    records: dict[tuple[str, int | None], FinalArtifact] = {}
    paths: set[str] = set()
    event_rms_tags: set[str | None] = set()
    for artifact in bundle.artifacts:
        key = (artifact.role, artifact.channel)
        if key in records or artifact.path in paths:
            issues.append("DUPLICATE_ARTIFACT")
            continue
        records[key] = artifact
        paths.add(artifact.path)
        if artifact.role not in _ROLES:
            issues.append("ARTIFACT_ROLE_UNKNOWN")
            continue
        if not _safe_path(artifact.path):
            issues.append("ARTIFACT_PATH_INVALID")
            continue
        if _safe_path(bundle.recording_root) and _safe_path(bundle.event_root):
            actual_root = (bundle.event_root if artifact.role.startswith("event_")
                           else bundle.recording_root)
            if not _is_contained(artifact.path, actual_root):
                issues.append("ARTIFACT_WRONG_ROOT")
            if (artifact.role.startswith("recorded_")
                    and _is_contained(artifact.path, bundle.event_root)):
                issues.append("ARTIFACT_WRONG_ROOT")

        if (artifact.role.endswith("_srt") and
                (artifact.channel is not None or
                 PurePosixPath(artifact.path).suffix != ".srt")):
            issues.append("ARTIFACT_FORMAT_INVALID")
        elif artifact.role.endswith("_video") and (
            not isinstance(artifact.channel, int)
            or isinstance(artifact.channel, bool)
            or artifact.channel not in channels
            or PurePosixPath(artifact.path).suffix not in _VIDEO_SUFFIXES
        ):
            issues.append("ARTIFACT_FORMAT_INVALID")
        # A file from a different session or channel must never be accepted
        # merely because its externally supplied metadata says otherwise.
        file_name = PurePosixPath(artifact.path).name
        pattern = {
            "recorded_video": _VIDEO_NAME,
            "event_video": _EVENT_VIDEO_NAME,
            "recorded_srt": _SRT_NAME,
            "event_srt": _EVENT_SRT_NAME,
        }[artifact.role]
        name_match = pattern.fullmatch(file_name)
        if name_match is None:
            issues.append("ARTIFACT_FILENAME_INVALID")
        else:
            if name_match.group("session") != bundle.session:
                issues.append("ARTIFACT_FILENAME_SESSION_MISMATCH")
            if artifact.role.startswith("event_"):
                event_rms_tags.add(name_match.group("rms"))
            if (artifact.role.endswith("_video")
                    and (not isinstance(artifact.channel, int)
                         or isinstance(artifact.channel, bool)
                         or int(name_match.group("channel")) != artifact.channel)):
                issues.append("ARTIFACT_FILENAME_CHANNEL_MISMATCH")

        if artifact.boot_id != bundle.boot_id:
            issues.append("ARTIFACT_BOOT_MISMATCH")
        if (artifact.session != bundle.session
                or not isinstance(artifact.sha256, str)
                or not _SHA256.fullmatch(artifact.sha256)
                or not _positive_int(artifact.size_bytes)
                or artifact.finalization_verified is not True):
            issues.append("FINAL_ARTIFACT_UNVERIFIED")
        if (not _positive_int(artifact.mtime_ns) or
                not _positive_int(bundle.completed_ns) or
                artifact.mtime_ns > bundle.completed_ns):
            issues.append("ARTIFACT_TIME_INVALID")
        if artifact.role.startswith("event_"):
            if artifact.path in previous:
                issues.append("STALE_EVENT_ARTIFACT")
            if not _positive_int(bundle.anchor_ns) or (
                    _positive_int(artifact.mtime_ns)
                    and artifact.mtime_ns <= bundle.anchor_ns):
                issues.append("STALE_EVENT_ARTIFACT")

    if len(event_rms_tags) > 1:
        issues.append("EVENT_RMS_TAG_MISMATCH")

    required = {("recorded_video", ch) for ch in channels}
    required |= {("event_video", ch) for ch in channels}
    required |= {("recorded_srt", None), ("event_srt", None)}
    if not required.issubset(records):
        issues.append("REQUIRED_FINAL_FILES_MISSING")
    for ch in channels:
        source = records.get(("recorded_video", ch))
        copied = records.get(("event_video", ch))
        if source is not None and copied is not None:
            if (copied.source_path != source.path
                    or copied.sha256 != source.sha256
                    or copied.size_bytes != source.size_bytes):
                issues.append("EVENT_COPY_PROVENANCE_UNVERIFIED")
    source = records.get(("recorded_srt", None))
    copied = records.get(("event_srt", None))
    if source is not None and copied is not None:
        if (copied.source_path != source.path
                or copied.sha256 != source.sha256
                or copied.size_bytes != source.size_bytes):
            issues.append("EVENT_COPY_PROVENANCE_UNVERIFIED")

    return tuple(dict.fromkeys(issues))
