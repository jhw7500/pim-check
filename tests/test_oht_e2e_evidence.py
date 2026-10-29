"""Fail-closed offline proofs for *direct-copy* OHT/ORD/VCM artifacts.

These are synthetic observations, not physical-board or network captures.
No real recording files, board state, SSH, OHT sender, or copies are accessed.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from oht_e2e_evidence import DirectCopyEvidence, FinalArtifact, evaluate_direct_copy_evidence


ANCHOR = 1_800_000_000_000_000_000
DONE = ANCHOR + 120_000_000_000
SESSION = "20261029_1240"
BOOT = "5c1762b0-374c-4b2d-adfb-b8b5419b0d80"
SOURCE_ROOT = "/mnt/sd_cam"
EVENT_ROOT = "/mnt/sd_cam/event"
SOURCE_VIDEO = f"{SOURCE_ROOT}/ABC123_20261029_124000-ch0.mp4"
EVENT_VIDEO = f"{EVENT_ROOT}/EVENT_ABC123_20261029_124000-ch0.mp4"
SOURCE_SRT = f"{SOURCE_ROOT}/ABC123_20261029_124000-data.srt"
EVENT_SRT = f"{EVENT_ROOT}/EVENT_ABC123_20261029_124000-data.srt"
VIDEO_SHA = "a" * 64
SRT_SHA = "b" * 64


@pytest.fixture
def valid_direct_copy():
    recorded_video = FinalArtifact(
        role="recorded_video",
        path=SOURCE_VIDEO,
        session=SESSION,
        boot_id=BOOT,
        channel=0,
        sha256=VIDEO_SHA,
        size_bytes=10_000,
        mtime_ns=ANCHOR + 20_000_000_000,
        finalization_verified=True,
    )
    event_video = FinalArtifact(
        role="event_video",
        path=EVENT_VIDEO,
        session=SESSION,
        boot_id=BOOT,
        channel=0,
        sha256=VIDEO_SHA,
        size_bytes=10_000,
        mtime_ns=ANCHOR + 90_000_000_000,
        finalization_verified=True,
        source_path=SOURCE_VIDEO,
    )
    recorded_srt = FinalArtifact(
        role="recorded_srt",
        path=SOURCE_SRT,
        session=SESSION,
        boot_id=BOOT,
        channel=None,
        sha256=SRT_SHA,
        size_bytes=2_000,
        mtime_ns=ANCHOR + 40_000_000_000,
        finalization_verified=True,
    )
    event_srt = FinalArtifact(
        role="event_srt",
        path=EVENT_SRT,
        session=SESSION,
        boot_id=BOOT,
        channel=None,
        sha256=SRT_SHA,
        size_bytes=2_000,
        mtime_ns=ANCHOR + 90_000_000_000,
        finalization_verified=True,
        source_path=SOURCE_SRT,
    )
    return DirectCopyEvidence(
        recording_root=SOURCE_ROOT,
        event_root=EVENT_ROOT,
        boot_id=BOOT,
        session=SESSION,
        enabled_channels=(0,),
        anchor_ns=ANCHOR,
        completed_ns=DONE,
        attempted_requests=1,
        request_frame_verified=True,
        response_frame_verified=True,
        request_frame_sha256="c" * 64,
        response_frame_sha256="d" * 64,
        request_command="COPY",
        expected_status_token="NODE-20261029-E2E",
        vcm_status_token="NODE-20261029-E2E",
        srt_status_token="NODE-20261029-E2E",
        srt_content_verified=True,
        preexisting_event_paths=(),
        preflight_blockers=(),
        artifacts=(recorded_video, event_video, recorded_srt, event_srt),
    )


def _mutate_artifact(bundle, role, **updates):
    return replace(
        bundle,
        artifacts=tuple(
            replace(a, **updates) if a.role == role else a
            for a in bundle.artifacts
        ),
    )


def _find(bundle, role):
    return next(a for a in bundle.artifacts if a.role == role)


def test_only_complete_direct_copy_evidence_can_be_internally_consistent(valid_direct_copy):
    assert evaluate_direct_copy_evidence(valid_direct_copy) == ()


@pytest.mark.parametrize("field,bad,expected", [
    ("preflight_blockers", None, "PREFLIGHT_EVIDENCE_MISSING"),
    ("preflight_blockers", ("DISK_LIMIT_REACHED",), "PREFLIGHT_BLOCKED"),
    ("boot_id", None, "BOOT_ID_MISSING"),
    ("session", "202610291240", "SESSION_INVALID"),
    ("session", "", "SESSION_INVALID"),
    ("enabled_channels", (), "CHANNEL_POLICY_INVALID"),
    ("enabled_channels", (0, 0), "CHANNEL_POLICY_INVALID"),
    ("enabled_channels", (True,), "CHANNEL_POLICY_INVALID"),
    ("enabled_channels", (4,), "CHANNEL_POLICY_INVALID"),
    ("anchor_ns", 0, "EVENT_TIMELINE_INVALID"),
    ("completed_ns", ANCHOR, "EVENT_TIMELINE_INVALID"),
    ("attempted_requests", 0, "REQUEST_COUNT_NOT_ONE"),
    ("attempted_requests", 2, "REQUEST_COUNT_NOT_ONE"),
    ("attempted_requests", True, "REQUEST_COUNT_NOT_ONE"),
    ("request_frame_verified", False, "OHT_FRAME_UNVERIFIED"),
    ("response_frame_verified", None, "OHT_FRAME_UNVERIFIED"),
    ("request_frame_sha256", None, "OHT_FRAME_UNVERIFIED"),
    ("response_frame_sha256", "invalid", "OHT_FRAME_UNVERIFIED"),
    ("request_command", "PRI", "OHT_FRAME_UNVERIFIED"),
    ("expected_status_token", None, "VCM_SRT_CORRELATION_UNVERIFIED"),
    ("expected_status_token", "X", "VCM_SRT_CORRELATION_UNVERIFIED"),
    ("vcm_status_token", "other", "VCM_SRT_CORRELATION_UNVERIFIED"),
    ("srt_status_token", "other", "VCM_SRT_CORRELATION_UNVERIFIED"),
    ("srt_content_verified", False, "VCM_SRT_CORRELATION_UNVERIFIED"),
    ("preexisting_event_paths", None, "EVENT_BASELINE_MISSING"),
    ("recording_root", None, "ARTIFACT_ROOTS_UNVERIFIED"),
    ("event_root", "/tmp/event", "ARTIFACT_ROOTS_UNVERIFIED"),
])
def test_omitted_or_ambiguous_transaction_fields_fail_closed(
    valid_direct_copy, field, bad, expected
):
    bundle = replace(valid_direct_copy, **{field: bad})
    assert expected in evaluate_direct_copy_evidence(bundle)


@pytest.mark.parametrize("role,updates,expected", [
    ("event_video", {"size_bytes": 0}, "FINAL_ARTIFACT_UNVERIFIED"),
    ("event_video", {"size_bytes": 10001}, "EVENT_COPY_PROVENANCE_UNVERIFIED"),
    ("event_srt", {"size_bytes": 2001}, "EVENT_COPY_PROVENANCE_UNVERIFIED"),
    ("event_video", {"boot_id": "other-boot"}, "ARTIFACT_BOOT_MISMATCH"),
    ("event_video", {"sha256": "not-sha256"}, "FINAL_ARTIFACT_UNVERIFIED"),
    ("event_video", {"finalization_verified": False}, "FINAL_ARTIFACT_UNVERIFIED"),
    ("event_video", {"session": "20261029_1239"}, "FINAL_ARTIFACT_UNVERIFIED"),
    ("event_video", {"path": EVENT_VIDEO + ".part"}, "ARTIFACT_FORMAT_INVALID"),
    ("event_srt", {"path": EVENT_SRT + ".part"}, "ARTIFACT_FORMAT_INVALID"),
    ("event_srt", {"path": f"{EVENT_ROOT}/.all_done"}, "ARTIFACT_FORMAT_INVALID"),
    ("recorded_video", {"path": SOURCE_VIDEO.replace(".mp4", ".log")},
     "ARTIFACT_FORMAT_INVALID"),
    ("event_video", {"channel": 2}, "ARTIFACT_FORMAT_INVALID"),
    ("event_srt", {"channel": 0}, "ARTIFACT_FORMAT_INVALID"),
    ("event_video", {"mtime_ns": ANCHOR}, "STALE_EVENT_ARTIFACT"),
    ("event_video", {"mtime_ns": ANCHOR - 1}, "STALE_EVENT_ARTIFACT"),
    ("event_video", {"mtime_ns": DONE + 1}, "ARTIFACT_TIME_INVALID"),
    ("event_video", {"path": "/other/event-copy.mp4"}, "ARTIFACT_WRONG_ROOT"),
    ("recorded_video", {"path": EVENT_VIDEO}, "ARTIFACT_WRONG_ROOT"),
    ("event_video", {"path": f"{EVENT_ROOT}/../escape.mp4"}, "ARTIFACT_PATH_INVALID"),
    ("event_video", {"source_path": "/mnt/sd_cam/other.mp4"},
     "EVENT_COPY_PROVENANCE_UNVERIFIED"),
    ("event_video", {"sha256": "c" * 64}, "EVENT_COPY_PROVENANCE_UNVERIFIED"),
    ("event_srt", {"source_path": SOURCE_VIDEO},
     "EVENT_COPY_PROVENANCE_UNVERIFIED"),
    ("event_srt", {"sha256": "c" * 64}, "EVENT_COPY_PROVENANCE_UNVERIFIED"),
])
def test_invalid_or_unproven_file_never_counts_as_completed_e2e(
    valid_direct_copy, role, updates, expected
):
    bundle = _mutate_artifact(valid_direct_copy, role, **updates)
    assert expected in evaluate_direct_copy_evidence(bundle)


@pytest.mark.parametrize("role", ["recorded_video", "event_video"])
def test_boolean_channel_is_not_a_valid_numeric_camera_channel(valid_direct_copy, role):
    """Python bool == int must not let an invalid channel satisfy E2E evidence."""
    bundle = _mutate_artifact(valid_direct_copy, role, channel=False)
    assert "ARTIFACT_FORMAT_INVALID" in evaluate_direct_copy_evidence(bundle)


def test_prior_event_file_cannot_be_reused_as_this_event(valid_direct_copy):
    bundle = replace(valid_direct_copy, preexisting_event_paths=(EVENT_VIDEO,))
    assert "STALE_EVENT_ARTIFACT" in evaluate_direct_copy_evidence(bundle)


@pytest.mark.parametrize("missing_role", [
    "recorded_video", "event_video", "recorded_srt", "event_srt"
])
def test_no_file_or_marker_substitutes_for_required_final_artifact(
    valid_direct_copy, missing_role
):
    files = tuple(a for a in valid_direct_copy.artifacts if a.role != missing_role)
    bundle = replace(valid_direct_copy, artifacts=files)
    assert "REQUIRED_FINAL_FILES_MISSING" in evaluate_direct_copy_evidence(bundle)


def test_copy_ack_without_files_is_not_a_pass(valid_direct_copy):
    bundle = replace(valid_direct_copy, artifacts=())
    assert "REQUIRED_FINAL_FILES_MISSING" in evaluate_direct_copy_evidence(bundle)


def test_duplicate_event_artifact_is_rejected(valid_direct_copy):
    bundle = replace(valid_direct_copy,
                     artifacts=valid_direct_copy.artifacts + (
                         _find(valid_direct_copy, "event_srt"),
                     ))
    assert "DUPLICATE_ARTIFACT" in evaluate_direct_copy_evidence(bundle)


def test_second_channel_requires_second_recording_and_copy(valid_direct_copy):
    bundle = replace(valid_direct_copy, enabled_channels=(0, 1))
    assert "REQUIRED_FINAL_FILES_MISSING" in evaluate_direct_copy_evidence(bundle)


def test_recording_source_may_predate_event_request(valid_direct_copy):
    """COPY chooses a prior recording session; only the event copy must be new."""
    bundle = _mutate_artifact(valid_direct_copy, "recorded_video",
                              mtime_ns=ANCHOR - 30_000_000_000)
    assert evaluate_direct_copy_evidence(bundle) == ()


def test_changed_event_content_requires_a_separate_tail_merge_contract(valid_direct_copy):
    """Tail/merged recordings must not falsely pass exact direct-copy checks."""
    bundle = _mutate_artifact(valid_direct_copy, "event_video", sha256="c" * 64)
    assert "EVENT_COPY_PROVENANCE_UNVERIFIED" in evaluate_direct_copy_evidence(bundle)
