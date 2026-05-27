"""tests/test_web_active.py — pim_web_viewer.active_hosts 검증.

multi-target viewer 가 enumerate 할 host 목록을 events/active.json 기반으로
반환한다. Step 1 의 run_stream.register_active_host 가 기록한 정보를 그대로
노출하고, 파일이 없거나 손상돼도 graceful 한 빈 응답을 돌려준다.
"""
from __future__ import annotations

import json
import os

import pim_web_viewer as v


def _patch_dir(monkeypatch, events_dir: str) -> None:
    monkeypatch.setattr(v, "_producer_events_dir", lambda: events_dir)


def test_active_hosts_empty_when_no_file(tmp_path, monkeypatch):
    events_dir = str(tmp_path / "events")
    os.makedirs(events_dir, exist_ok=True)
    _patch_dir(monkeypatch, events_dir)
    out = v.active_hosts()
    assert out == {"hosts": []}


def test_active_hosts_returns_registered_entries(tmp_path, monkeypatch):
    events_dir = str(tmp_path / "events")
    os.makedirs(events_dir, exist_ok=True)
    _patch_dir(monkeypatch, events_dir)
    # Step 1 의 register_active_host 가 만든 파일과 같은 형태를 시뮬레이션.
    payload = {"hosts": [
        {"host": "192.168.0.5", "slug": "192-168-0-5", "plan": "smoke",
         "board": "192.168.0.5",
         "current": "by-target/192-168-0-5/current.jsonl",
         "run": "by-target/192-168-0-5/T1_smoke_192-168-0-5.jsonl",
         "started_at": 1700000000.0},
        {"host": "host-b", "slug": "host-b", "plan": "comprehensive",
         "board": "host-b",
         "current": "by-target/host-b/current.jsonl",
         "run": "by-target/host-b/T2_comprehensive_host-b.jsonl",
         "started_at": 1700000010.0},
    ]}
    with open(os.path.join(events_dir, "active.json"), "w") as f:
        json.dump(payload, f)
    out = v.active_hosts()
    hosts = {h["host"]: h for h in out["hosts"]}
    assert set(hosts) == {"192.168.0.5", "host-b"}
    assert hosts["192.168.0.5"]["slug"] == "192-168-0-5"
    assert hosts["host-b"]["plan"] == "comprehensive"


def test_active_hosts_graceful_on_corrupt_json(tmp_path, monkeypatch):
    # 손상된 JSON 도 viewer 가 멈추지 않도록 빈 hosts 로 fallback.
    events_dir = str(tmp_path / "events")
    os.makedirs(events_dir, exist_ok=True)
    _patch_dir(monkeypatch, events_dir)
    with open(os.path.join(events_dir, "active.json"), "w") as f:
        f.write("not-json-at-all")
    assert v.active_hosts() == {"hosts": []}


def test_api_active_route_returns_json(tmp_path, monkeypatch):
    """HTTP layer thin-wrapper — /api/active 라우트가 active_hosts() 결과를
    JSON 으로 직렬화해 200 으로 돌려준다. _Handler 직접 단위 테스트.
    """
    events_dir = str(tmp_path / "events")
    os.makedirs(events_dir, exist_ok=True)
    _patch_dir(monkeypatch, events_dir)
    payload = {"hosts": [{"host": "h", "slug": "h"}]}
    with open(os.path.join(events_dir, "active.json"), "w") as f:
        json.dump(payload, f)

    # _Handler 의 do_GET 라우트 분기만 검증 — _send_json 으로 응답 캡처.
    captured = {}

    class _FakeHandler(v._Handler):
        def __init__(self_inner):  # bypass BaseHTTPRequestHandler init
            pass

        def _send_json(self_inner, code, body):
            captured["code"] = code
            captured["body"] = body

    h = _FakeHandler()
    h.path = "/api/active"
    h.do_GET()
    assert captured["code"] == 200
    # hosts 는 그대로 + max_concurrent 가 server 의 cap 을 노출 (UI 가 동기화).
    assert captured["body"]["hosts"] == [{"host": "h", "slug": "h"}]
    assert captured["body"]["max_concurrent"] == v.MAX_CONCURRENT_TARGETS
