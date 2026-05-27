"""tests/test_web_multi_target_start.py — pim_web_viewer.start_run 의 multi-target 확장 검증.

기존 single-host 요청 ({plan, host, user, password}) 동작은 그대로 유지하면서,
``targets: [{host,user,password}, ...]`` 배열 형태를 추가로 받아 host 별로
pim_check.py 를 spawn 한다. per-host conflict (자기 자신의 다른 target spawn)
체크는 events/by-target/<slug>/ scope 로 분리된다.
"""
from __future__ import annotations

import os

import pim_web_viewer as v
import run_control
import run_stream


class _FakeProc:
    def __init__(self, pid: int):
        self.pid = pid


def _patch(monkeypatch, tmp_path, plans=("smoke",)):
    monkeypatch.setattr(v, "_producer_events_dir", lambda: str(tmp_path))
    monkeypatch.setattr(v, "_list_plans", lambda: list(plans))
    # 기본은 stream 없음 → start_run 의 legacy single-host external-run 가드를 통과.
    monkeypatch.setattr(v, "build_state", lambda p: {"exists": False})


def test_multi_target_spawns_one_per_host(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    spawned = []
    pid_counter = [10000]

    def fake_popen(argv, **kw):
        pid_counter[0] += 1
        # 캡처: --host 값 + env 의 PIM_PASSWORD
        host_idx = argv.index("--host") + 1
        spawned.append({
            "host": argv[host_idx],
            "plan": argv[argv.index("--plan") + 1],
            "password": kw.get("env", {}).get("PIM_PASSWORD"),
            "pid": pid_counter[0],
        })
        return _FakeProc(pid_counter[0])

    monkeypatch.setattr(v.subprocess, "Popen", fake_popen)
    code, body = v.start_run({
        "plan": "smoke",
        "targets": [
            {"host": "1.2.3.4", "user": "root", "password": "p1"},
            {"host": "5.6.7.8", "user": "root", "password": "p2"},
        ],
    })
    assert code == 200, body
    assert body["ok"] is True
    assert {s["host"] for s in spawned} == {"1.2.3.4", "5.6.7.8"}
    # 각 target 의 password 가 자기 자신만 env 로 전달 (다른 target 의 비밀번호 누설 X).
    pw_by_host = {s["host"]: s["password"] for s in spawned}
    assert pw_by_host == {"1.2.3.4": "p1", "5.6.7.8": "p2"}
    # 응답에 host 별 (pid, plan) 결과 요약이 포함.
    started = {h["host"]: h for h in body["started"]}
    assert set(started) == {"1.2.3.4", "5.6.7.8"}
    assert all(h["pid"] > 0 for h in body["started"])


def test_multi_target_per_host_control_file_written(tmp_path, monkeypatch):
    # 각 host 의 per-target events_dir 에 control 파일이 기록돼야 한다.
    _patch(monkeypatch, tmp_path)
    monkeypatch.setattr(v.subprocess, "Popen", lambda *a, **k: _FakeProc(33333))
    code, _ = v.start_run({
        "plan": "smoke",
        "targets": [{"host": "host-a", "user": "root", "password": "p"}],
    })
    assert code == 200
    per_target = run_stream.target_events_dir(str(tmp_path), "host-a")
    info = run_control.read_control(per_target)
    assert info is not None
    assert info["host"] == "host-a"


def test_multi_target_enforces_max_concurrent(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    # MAX_CONCURRENT_TARGETS 초과 시 spawn 전에 400 으로 reject.
    too_many = [
        {"host": f"10.0.0.{i}", "user": "root", "password": "p"}
        for i in range(1, v.MAX_CONCURRENT_TARGETS + 2)
    ]
    called = {"popen": 0}
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", called["popen"] + 1) or _FakeProc(1))
    code, body = v.start_run({"plan": "smoke", "targets": too_many})
    assert code == 400 and not body["ok"]
    assert called["popen"] == 0  # 한 개도 spawn 안 됨 (validation-first)


def test_multi_target_rejects_invalid_host_in_array(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    called = {"popen": 0}
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", called["popen"] + 1) or _FakeProc(1))
    code, body = v.start_run({
        "plan": "smoke",
        "targets": [
            {"host": "1.2.3.4", "user": "root", "password": "p"},
            {"host": "../../etc/passwd", "user": "root", "password": "p"},  # invalid
        ],
    })
    assert code == 400 and not body["ok"]
    # 하나라도 invalid 면 전체 spawn 거부 — partial 시작은 stop 책임이 복잡해진다.
    assert called["popen"] == 0


def test_multi_target_409_when_per_host_already_active(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    # host-b 의 per-target events_dir 에 살아있는 control 미리 기록 → conflict.
    per_target_b = run_stream.target_events_dir(str(tmp_path), "host-b")
    os.makedirs(per_target_b, exist_ok=True)
    run_control.write_control(per_target_b, {"pid": os.getpid(), "plan": "smoke",
                                              "host": "host-b"})
    called = {"popen": 0}
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", called["popen"] + 1) or _FakeProc(1))
    code, body = v.start_run({
        "plan": "smoke",
        "targets": [
            {"host": "host-a", "user": "root", "password": "p"},
            {"host": "host-b", "user": "root", "password": "p"},  # conflict
        ],
    })
    assert code == 409 and not body["ok"]
    assert called["popen"] == 0  # 한 개도 spawn 안 됨 (all-or-nothing)


def test_multi_target_empty_targets_array_rejected(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no spawn")))
    code, body = v.start_run({"plan": "smoke", "targets": []})
    assert code == 400 and not body["ok"]


def test_legacy_single_host_path_unchanged(tmp_path, monkeypatch):
    # backward compat — targets 없으면 기존 single-host 동작 그대로.
    _patch(monkeypatch, tmp_path)
    cap = {}

    def fake_popen(argv, **kw):
        cap["argv"] = argv
        cap["env"] = kw.get("env")
        return _FakeProc(77777)

    monkeypatch.setattr(v.subprocess, "Popen", fake_popen)
    code, body = v.start_run({"plan": "smoke", "host": "1.1.1.1",
                              "user": "root", "password": "p"})
    assert code == 200 and body["ok"]
    assert "--host" in cap["argv"]
    assert cap["env"]["PIM_PASSWORD"] == "p"
