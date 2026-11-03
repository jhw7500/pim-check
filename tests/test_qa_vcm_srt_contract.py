"""Deterministic offline VCM SRT marker/async write race reproducer.

These tests exercise a model and an untrusted-event ordering contract.
They do not compile or run product VCM code, and cannot unblock PIM hardware.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from queue import Queue
from threading import Event, Thread

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "qa_vcm_srt_contract.py"
spec = importlib.util.spec_from_file_location("qa_vcm_srt_contract", SOURCE)
assert spec is not None and spec.loader is not None
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def complete_trace(session: str = "20261102_0950") -> dict[str, object]:
    stages = list(policy._STAGES)
    events = []
    for n, stage in enumerate(stages, 1):
        item: dict[str, object] = {
            "seq": n,
            "session": session,
            "stage": stage,
            "ok": True,
        }
        if stage == "queue_drained":
            item["pending"] = 0
        if stage == "inflight_drained":
            item["inflight"] = 0
        if stage == "append_checked":
            item["bytes_written"] = 512
        events.append(item)
    return {"schema": 1, "session": session, "events": events}


def _event(trace: dict[str, object], stage: str) -> dict[str, object]:
    return next(e for e in trace["events"] if e["stage"] == stage)


def test_async_vcm_legacy_order_can_mark_previous_session_before_write():
    """Force the race: queue empty while writer still holds an in-flight item."""
    queue: Queue[str] = Queue()
    append_started = Event()
    release_append = Event()
    on_disk: list[str] = []

    def worker() -> None:
        item = queue.get()
        append_started.set()
        if release_append.wait(2):
            on_disk.append(item)
        queue.task_done()

    queue.put("previous-session-srt")
    thread = Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert append_started.wait(2)
        assert queue.qsize() == 0  # Dequeued, NOT yet durable.
        # Model the legacy VCM new-session previous-session srt_done marker.
        previous_session_marker = True
        assert previous_session_marker and not on_disk
        # A deliberately premature trace must fail the reviewer.
        trace = complete_trace()
        trace["events"][3]["inflight"] = 1
        result = policy.review_srt_done_evidence(trace)
        assert result["decision"] == "BLOCK"
        assert result["reason_codes"] == ["QUEUED_OR_INFLIGHT_IO_REMAINING"]
    finally:
        release_append.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert on_disk == ["previous-session-srt"]


def test_complete_synthetic_order_requires_review_not_hardware_access():
    result = policy.review_srt_done_evidence(complete_trace())
    assert result == {
        "schema": 1,
        "decision": "REVIEW_REQUIRED",
        "reason_codes": ["UNTRUSTED_OFFLINE_EVENT_TRACE"],
        "logical_order_valid": True,
        "trusted_installed_evidence": False,
        "hardware_execution_authorized": False,
    }


@pytest.mark.parametrize("missing", [
    "writer_inhibited", "producer_stopped", "queue_drained",
    "inflight_drained", "append_checked", "file_synced",
    "directory_synced", "fd_closed", "srt_done_published",
])
def test_missing_prerequisite_blocks_premature_marker(missing: str):
    trace = complete_trace()
    trace["events"] = [e for e in trace["events"] if e["stage"] != missing]
    assert policy.review_srt_done_evidence(trace)["decision"] == "BLOCK"


def test_marker_before_file_sync_is_rejected():
    trace = complete_trace()
    events = trace["events"]
    events[5]["stage"], events[8]["stage"] = events[8]["stage"], events[5]["stage"]
    assert policy.review_srt_done_evidence(trace)["reason_codes"] == ["MARKER_OR_STAGE_OUT_OF_ORDER"]


@pytest.mark.parametrize("stage,field,value", [
    ("queue_drained", "pending", 1),
    ("inflight_drained", "inflight", 1),
    ("append_checked", "bytes_written", 0),
    ("append_checked", "bytes_written", True),
])
def test_unfinished_io_never_admits(stage: str, field: str, value: object):
    trace = complete_trace()
    _event(trace, stage)[field] = value
    result = policy.review_srt_done_evidence(trace)
    assert result["decision"] == "BLOCK"
    assert not result["hardware_execution_authorized"]


@pytest.mark.parametrize("stage", list(policy._STAGES))
def test_any_stage_failure_blocks(stage: str):
    trace = complete_trace()
    _event(trace, stage)["ok"] = False
    assert policy.review_srt_done_evidence(trace)["decision"] == "BLOCK"


@pytest.mark.parametrize("field,value", [
    ("seq", True), ("seq", 0), ("session", "another_session"),
    ("stage", "srt_done_published"), ("ok", "true"),
])
def test_malformed_spoofed_events_are_rejected(field: str, value: object):
    trace = complete_trace()
    trace["events"][0][field] = value
    assert policy.review_srt_done_evidence(trace)["decision"] == "BLOCK"


def test_extra_post_marker_write_and_missing_doc_are_rejected():
    trace = complete_trace()
    trace["events"].append({"seq": 10, "stage": "srt_write", "ok": True})
    assert policy.review_srt_done_evidence(trace)["decision"] == "BLOCK"
    assert policy.review_srt_done_evidence({})["decision"] == "BLOCK"
    assert policy.review_srt_done_evidence(None)["decision"] == "BLOCK"


def test_cli_fail_closed_even_on_perfect_trace(tmp_path, capsys):
    file = tmp_path / "trace.json"
    file.write_text(json.dumps(complete_trace()), encoding="utf-8")
    assert policy.main([str(file)]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["decision"] == "REVIEW_REQUIRED"
    assert output["hardware_execution_authorized"] is False


def test_cli_malformed_or_missing_trace_is_blocked(tmp_path, capsys):
    file = tmp_path / "bad.json"
    file.write_text("{malformed", encoding="utf-8")
    assert policy.main([str(file)]) == 3
    assert json.loads(capsys.readouterr().out)["decision"] == "BLOCK"
    assert policy.main([str(tmp_path / "missing.json")]) == 3
    assert json.loads(capsys.readouterr().out)["decision"] == "BLOCK"
