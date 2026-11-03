#!/usr/bin/env python3
"""Offline-only VCM SRT completion ordering review.

Inputs are caller-provided *untrusted* event traces, not authenticated PIM
runtime evidence. A valid trace is REVIEW_REQUIRED, never permission to
acquire a board lease, change camera settings, or publish srt_done.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

# Queue empty does NOT imply that a worker has completed an in-flight append.
# All events must be tied to the same exact recording session.
_STAGES = (
    "writer_inhibited",
    "producer_stopped",
    "queue_drained",
    "inflight_drained",
    "append_checked",
    "file_synced",
    "directory_synced",
    "fd_closed",
    "srt_done_published",
)
_COUNTS = {
    "queue_drained": ("pending", 0),
    "inflight_drained": ("inflight", 0),
}
_MAX_EVIDENCE_BYTES = 64 * 1024


def _outcome(decision: str, reason: str, *, ordered: bool = False) -> dict[str, object]:
    return {
        "schema": 1,
        "decision": decision,
        "reason_codes": [reason],
        "logical_order_valid": ordered,
        "trusted_installed_evidence": False,
        "hardware_execution_authorized": False,
    }


def review_srt_done_evidence(document: Any) -> dict[str, object]:
    """Check logical stage ordering only; untrusted events NEVER grant access."""
    if type(document) is not dict or set(document) != {"schema", "session", "events"}:
        return _outcome("BLOCK", "INVALID_DOCUMENT_SCHEMA")
    if type(document["schema"]) is not int or document["schema"] != 1:
        return _outcome("BLOCK", "INVALID_DOCUMENT_SCHEMA")
    session = document["session"]
    if type(session) is not str or re.fullmatch(r"[0-9]{8}_[0-9]{4}", session) is None:
        return _outcome("BLOCK", "INVALID_SESSION")
    events = document["events"]
    if type(events) is not list or len(events) != len(_STAGES):
        return _outcome("BLOCK", "INCOMPLETE_OR_EXTRA_EVENTS")

    for index, (stage, event) in enumerate(zip(_STAGES, events), start=1):
        if type(event) is not dict:
            return _outcome("BLOCK", "INVALID_EVENT")
        fields = {"seq", "session", "stage", "ok"}
        if stage in _COUNTS:
            fields.add(_COUNTS[stage][0])
        if stage == "append_checked":
            fields.update(("bytes_expected", "bytes_written"))
        if set(event) != fields:
            return _outcome("BLOCK", "INVALID_EVENT_FIELDS")
        # bool is an int subclass, but must never be accepted as a sequence.
        if type(event["seq"]) is not int or event["seq"] != index:
            return _outcome("BLOCK", "INVALID_EVENT_SEQUENCE")
        if type(event["session"]) is not str or event["session"] != session:
            return _outcome("BLOCK", "SESSION_MISMATCH")
        if type(event["stage"]) is not str or event["stage"] != stage:
            return _outcome("BLOCK", "MARKER_OR_STAGE_OUT_OF_ORDER")
        if event["ok"] is not True:
            return _outcome("BLOCK", "STAGE_FAILED_OR_UNVERIFIED")
        if stage in _COUNTS:
            key, required_count = _COUNTS[stage]
            if type(event[key]) is not int or event[key] != required_count:
                return _outcome("BLOCK", "QUEUED_OR_INFLIGHT_IO_REMAINING")
        if stage == "append_checked":
            # Session-total expected bytes must match acknowledged bytes; a
            # positive short write is not completion. Caller evidence remains
            # untrusted even when counts match exactly.
            expected, written = event["bytes_expected"], event["bytes_written"]
            if (type(expected) is not int or expected <= 0
                    or type(written) is not int or written <= 0):
                return _outcome("BLOCK", "APPEND_RESULT_UNVERIFIED")
            if written != expected:
                return _outcome("BLOCK", "APPEND_BYTE_COUNT_MISMATCH")

    # Even a perfect synthetic trace is not a trusted Owner/Writer/Fsync attestation.
    return _outcome("REVIEW_REQUIRED", "UNTRUSTED_OFFLINE_EVENT_TRACE", ordered=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="Offline local JSON event trace; never a live-board probe")
    args = parser.parse_args(argv)
    try:
        with args.evidence.open("rb") as handle:
            raw = handle.read(_MAX_EVIDENCE_BYTES + 1)
        if len(raw) > _MAX_EVIDENCE_BYTES:
            raise ValueError("too large")
        payload = json.loads(raw)
        result = review_srt_done_evidence(payload)
    except (OSError, ValueError, UnicodeError):
        result = _outcome("BLOCK", "EVIDENCE_FILE_UNAVAILABLE_OR_INVALID")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    # Neither BLOCK nor REVIEW_REQUIRED exits 0. This tool is not admission.
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
