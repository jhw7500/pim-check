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


def test_multi_target_cleans_up_when_later_spawn_fails(tmp_path, monkeypatch):
    """spawn 도중 OSError 등으로 실패하면 이미 spawn 한 자식의 control 파일이
    정리되어 좀비 control 이 남지 않아야 한다.

    1번 target 은 정상 spawn → control 작성, 2번 target 의 Popen 이 OSError 를
    던지면 cleanup 단계가 1번 target 의 control 을 지우고 SIGTERM 전송해야 한다.
    """
    _patch(monkeypatch, tmp_path)
    state = {"calls": 0, "killed": []}

    def fake_popen(argv, **kw):
        state["calls"] += 1
        if state["calls"] >= 2:
            raise OSError("simulated spawn failure")
        # 1번은 성공 — 살아있는 pid 반환 (자기 자신, kill 영향 없게 SIG 0 만 보내려면
        # 별도 sleep 자식 띄워야 하지만 cleanup 호출만 검증한다).
        return _FakeProc(99999)

    monkeypatch.setattr(v.subprocess, "Popen", fake_popen)
    # cleanup 의 _signal_pid 호출도 캡처 — 실제로 죽이지 말고 호출만 확인.
    monkeypatch.setattr(v, "_signal_pid",
                        lambda pid, sig: state["killed"].append((pid, sig)))
    # pid_alive 는 항상 False — SIGTERM 이후 좀비 점검 path 가 fast-exit 되도록.
    monkeypatch.setattr(v.run_control, "pid_alive", lambda pid: False)

    code, body = v.start_run({
        "plan": "smoke",
        "targets": [
            {"host": "host-a", "user": "root", "password": "p"},
            {"host": "host-b", "user": "root", "password": "p"},
        ],
    })
    assert code == 500 and not body["ok"]
    # 1번 target 은 spawn 됐고 cleanup 단계에서 SIGTERM 받았다.
    assert (99999, v.signal.SIGTERM) in state["killed"]
    # 1번 target 의 control 파일이 cleanup 으로 삭제됐다 (좀비 control 방지).
    per_dir_a = run_stream.target_events_dir(str(tmp_path), "host-a")
    assert run_control.read_control(per_dir_a) is None
    # 응답 body 에 partial_started 가 포함돼 caller 가 후속 stop 결정 가능.
    assert body.get("partial_started") == [
        {"host": "host-a", "plan": "smoke", "pid": 99999}
    ]


def test_multi_target_dedupes_by_slug_not_raw(tmp_path, monkeypatch):
    """raw host 가 달라도 host_slug 가 같으면 (예: a.b 와 a-b, Host-A 와 host-a)
    같은 by-target/<slug>/ 를 두 자식이 쓰게 되어 control / current.jsonl 경합.
    """
    _patch(monkeypatch, tmp_path)
    called = {"popen": 0}
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", called["popen"] + 1) or _FakeProc(1))
    code, body = v.start_run({
        "plan": "smoke",
        "targets": [
            {"host": "a.b", "user": "root", "password": "p"},
            {"host": "a-b", "user": "root", "password": "p"},  # slug 충돌
        ],
    })
    assert code == 400 and not body["ok"]
    assert "slug" in body["error"]
    assert called["popen"] == 0


def test_multi_target_rejects_null_host(tmp_path, monkeypatch):
    """JSON {"host": null} 이 str(None)="None" 으로 잘못 통과되지 않도록 차단."""
    _patch(monkeypatch, tmp_path)
    called = {"popen": 0}
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", called["popen"] + 1) or _FakeProc(1))
    code, body = v.start_run({
        "plan": "smoke",
        "targets": [{"host": None, "user": "root", "password": "p"}],
    })
    assert code == 400 and not body["ok"]
    assert called["popen"] == 0


def test_stop_run_per_host(tmp_path, monkeypatch):
    """stop_run({host: ...}) 가 per-host control 만 종료한다."""
    _patch(monkeypatch, tmp_path)
    import subprocess as _sp
    proc = _sp.Popen(["sleep", "30"], start_new_session=True)
    per_dir = run_stream.target_events_dir(str(tmp_path), "host-x")
    os.makedirs(per_dir, exist_ok=True)
    run_control.write_control(per_dir, {"pid": proc.pid, "plan": "smoke",
                                         "host": "host-x"})
    code, body = v.stop_run({"host": "host-x"})
    assert code == 200 and body["ok"] and body["stopped"] is True
    assert body["pid"] == proc.pid
    proc.wait(timeout=5)
    assert run_control.read_control(per_dir) is None  # 정리됨


def test_stop_run_bulk_targets(tmp_path, monkeypatch):
    """stop_run({targets: [hostA, hostB]}) 가 모든 host 를 종료하고 host 별 결과 반환."""
    _patch(monkeypatch, tmp_path)
    import subprocess as _sp
    procs = {}
    for h in ("host-a", "host-b"):
        p = _sp.Popen(["sleep", "30"], start_new_session=True)
        procs[h] = p
        per_dir = run_stream.target_events_dir(str(tmp_path), h)
        os.makedirs(per_dir, exist_ok=True)
        run_control.write_control(per_dir, {"pid": p.pid, "plan": "smoke", "host": h})
    code, body = v.stop_run({"targets": ["host-a", "host-b"]})
    assert code == 200 and body["ok"]
    by_host = {r["host"]: r for r in body["stopped"]}
    assert by_host["host-a"]["stopped"] is True
    assert by_host["host-b"]["stopped"] is True
    for h, p in procs.items():
        p.wait(timeout=5)
    # control 파일 모두 정리됨.
    for h in ("host-a", "host-b"):
        per_dir = run_stream.target_events_dir(str(tmp_path), h)
        assert run_control.read_control(per_dir) is None


def test_stop_run_rejects_invalid_host_in_targets(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    code, body = v.stop_run({"targets": ["host-a", "../../etc"]})
    assert code == 400 and not body["ok"]


def test_stop_run_legacy_no_args_works(tmp_path, monkeypatch):
    """기존 stop_run() (no-args) 동작은 100% 호환 — 단일 런 path."""
    _patch(monkeypatch, tmp_path)
    code, body = v.stop_run()
    assert code == 200 and body["ok"] and body["stopped"] is False


def test_stop_run_rejects_non_dict_body_at_http_layer(tmp_path, monkeypatch):
    """/stop POST 가 dict 가 아닌 JSON body (list, 원시값) 를 400 으로 거부.

    HTTP layer 검증 — do_POST 가 stop_run 으로 전달하기 전에 type guard.
    stop_run 이 list 를 받으면 p.get("targets") 에서 AttributeError 가 발생해
    unformatted 500 으로 전파되는 것을 방지.
    """
    _patch(monkeypatch, tmp_path)
    captured = {}

    class _FakeHandler(v._Handler):
        def __init__(self_inner):
            pass

        def _send_json(self_inner, code, body):
            captured["code"] = code
            captured["body"] = body

        def _read_json(self_inner):
            return [1, 2, 3]  # dict 가 아닌 valid JSON

    h = _FakeHandler()
    h.path = "/stop"
    h.do_POST()
    assert captured["code"] == 400 and not captured["body"]["ok"]


def test_stop_run_bulk_runs_in_parallel(tmp_path, monkeypatch):
    """bulk stop 이 host 별로 순차가 아닌 병렬 실행되는지 확인.

    shared threading.Barrier 로 4 worker 가 *동시에* 진입해야만 release 되는
    조건을 둔다 — 순차 실행이라면 첫 worker 가 barrier 에서 영원히 대기
    (다른 worker 가 아직 시작하지 않음) → 테스트 타임아웃으로 명확히 실패.
    이 패턴은 sleep-기반 임계값 (CI 느려서 false fail) 보다 안정적이다.
    """
    import threading as _th

    _patch(monkeypatch, tmp_path)
    n = 4
    barrier = _th.Barrier(n, timeout=2.0)

    def parallel_only_stop(events_dir):
        # n 개 worker 가 모두 barrier 에 도달해야 통과 — 순차면 영원히 대기.
        barrier.wait()
        return {"stopped": True, "pid": 1}

    monkeypatch.setattr(v, "_stop_in_events_dir", parallel_only_stop)
    code, body = v.stop_run({"targets": [f"host-{c}" for c in "abcd"]})
    assert code == 200 and body["ok"]
    # 결과는 입력 순서 보존.
    assert [r["host"] for r in body["stopped"]] == [f"host-{c}" for c in "abcd"]


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
