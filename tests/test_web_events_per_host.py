"""tests/test_web_events_per_host.py — pim_web_viewer.host_events_state 검증.

multi-target viewer 가 /api/events?host=<host> 를 폴링해 host 별 state 를
받아간다. events/by-target/<slug>/current.jsonl 경로로 해석해 기존
build_state 를 재사용한다.
"""
from __future__ import annotations

import json
import os

import pim_web_viewer as v
import run_stream


def _patch_dir(monkeypatch, events_dir: str) -> None:
    monkeypatch.setattr(v, "_producer_events_dir", lambda: events_dir)


def _write_jsonl(path: str, events: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_host_events_state_returns_per_target_state(tmp_path, monkeypatch):
    events_dir = str(tmp_path / "events")
    _patch_dir(monkeypatch, events_dir)
    # per-target current.jsonl 시뮬레이션 — 정확히 by-target/<slug>/current.jsonl 위치.
    slug = run_stream.host_slug("192.168.0.5")
    per_target_dir = os.path.join(events_dir, run_stream.BY_TARGET_DIR, slug)
    run_path = os.path.join(per_target_dir, "T1_smoke_192-168-0-5.jsonl")
    _write_jsonl(run_path, [
        {"event_type": "run_start", "plan": "smoke", "board": "192.168.0.5",
         "elapsed_s": 0, "cases": ["c1", "c2"], "total_cases": 2},
        {"event_type": "case_end", "case_name": "c1", "phase": "validate",
         "result": "pass", "completed_cases": 1, "pass_count": 1, "fail_count": 0,
         "avg_case_duration_s": 2.0, "elapsed_s": 2},
    ])
    # symlink 로 current.jsonl → run_path
    os.symlink(os.path.basename(run_path),
               os.path.join(per_target_dir, run_stream.CURRENT_SYMLINK_NAME))

    st = v.host_events_state("192.168.0.5")
    assert st["exists"] is True
    assert st["plan"] == "smoke"
    assert st["completed"] == 1 and st["total"] == 2
    assert st["pass"] == 1


def test_host_events_state_missing_host_returns_not_exists(tmp_path, monkeypatch):
    events_dir = str(tmp_path / "events")
    os.makedirs(events_dir, exist_ok=True)
    _patch_dir(monkeypatch, events_dir)
    # host 의 per-target dir/current.jsonl 자체가 없음.
    st = v.host_events_state("never-started")
    assert st == {"exists": False}


def test_host_events_state_rejects_unsafe_host(tmp_path, monkeypatch):
    # 잘못된 host 문자(slash, ..) 는 빈 state 로 reject — directory traversal 방어.
    _patch_dir(monkeypatch, str(tmp_path / "events"))
    for bad in ["../../etc", "a/b", "", None]:
        assert v.host_events_state(bad) == {"exists": False}


def test_api_events_route_uses_host_query_param(tmp_path, monkeypatch):
    """HTTP layer — /api/events?host=<host> 가 host_events_state(host) 결과를 반환."""
    events_dir = str(tmp_path / "events")
    _patch_dir(monkeypatch, events_dir)
    # 존재하지 않는 host 라도 그냥 {exists:false} 반환 (200).
    captured = {}

    class _FakeHandler(v._Handler):
        def __init__(self_inner):
            pass

        def _send_json(self_inner, code, body):
            captured["code"] = code
            captured["body"] = body

        def _send(self_inner, body_bytes, ctype):
            captured["code"] = 200
            captured["body"] = json.loads(body_bytes.decode())

    h = _FakeHandler()
    h.path = "/api/events?host=host-a"
    h.do_GET()
    assert captured["code"] == 200
    assert captured["body"] == {"exists": False}


def test_api_events_missing_host_query_returns_400(tmp_path, monkeypatch):
    _patch_dir(monkeypatch, str(tmp_path / "events"))
    captured = {}

    class _FakeHandler(v._Handler):
        def __init__(self_inner):
            pass

        def _send_json(self_inner, code, body):
            captured["code"] = code
            captured["body"] = body

    h = _FakeHandler()
    h.path = "/api/events"  # host 파라미터 없음
    h.do_GET()
    assert captured["code"] == 400
    assert captured["body"]["ok"] is False
