"""tests/test_web_control.py - pim_web_viewer 제어(start/stop) 로직 검증.

subprocess.Popen 을 목으로 대체해 실제 spawn 없이 검증/가드/직렬화/비밀번호
전달 방식을 확인한다.
"""
from __future__ import annotations

import os
import subprocess

import pim_web_viewer as v
import run_control


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid


def _patch_common(monkeypatch, tmp_path, plans=("smoke",), state=None):
    monkeypatch.setattr(v, "_producer_events_dir", lambda: str(tmp_path))
    monkeypatch.setattr(v, "_list_plans", lambda: list(plans))
    monkeypatch.setattr(v, "build_state",
                        lambda p: state if state is not None else {"exists": False})


def test_start_run_uses_env_not_argv(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    cap = {}

    def fake_popen(argv, **kw):
        cap["argv"] = argv
        cap["env"] = kw.get("env")
        return _FakeProc(99999)

    monkeypatch.setattr(v.subprocess, "Popen", fake_popen)
    code, body = v.start_run({"plan": "smoke", "host": "1.2.3.4",
                              "user": "root", "password": "sekret"})
    assert code == 200 and body["ok"]
    # 비밀번호는 argv 에 없고 env(PIM_PASSWORD)로만 전달
    assert "sekret" not in cap["argv"]
    assert "--password" not in cap["argv"]
    assert cap["env"]["PIM_PASSWORD"] == "sekret"
    # 제어 상태 파일 기록
    info = run_control.read_control(str(tmp_path))
    assert info["pid"] == 99999 and info["plan"] == "smoke"


def test_start_run_rejects_unknown_plan(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: pytest_fail_popen())
    code, body = v.start_run({"plan": "evil", "host": "h",
                              "user": "root", "password": "x"})
    assert code == 400 and not body["ok"]


def pytest_fail_popen():
    raise AssertionError("Popen should not be called on validation failure")


def test_start_run_409_when_external_run_live(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path,
                  state={"exists": True, "run_ended": False, "producer_lost": False})
    called = {"popen": False}
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", True))
    code, body = v.start_run({"plan": "smoke", "host": "1.2.3.4",
                              "user": "root", "password": "x"})
    assert code == 409 and not called["popen"]


def test_start_run_409_when_managed_run_active(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    # 살아있는 PID(자기 자신)로 제어 파일 기록 → active 로 감지
    run_control.write_control(str(tmp_path),
                              {"pid": os.getpid(), "plan": "smoke", "host": "h"})
    called = {"popen": False}
    monkeypatch.setattr(v.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", True))
    code, body = v.start_run({"plan": "smoke", "host": "1.2.3.4",
                              "user": "root", "password": "x"})
    assert code == 409 and not called["popen"]


def test_stop_run_no_managed_run(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    code, body = v.stop_run()
    assert code == 200 and body["stopped"] is False


def test_stop_run_terminates_process(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    # start_new_session=True: 자체 프로세스 그룹 → killpg 가 테스트 러너를 안 죽인다.
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    run_control.write_control(str(tmp_path),
                              {"pid": proc.pid, "plan": "smoke", "host": "h"})
    code, body = v.stop_run()
    assert code == 200 and body["stopped"] is True and body["pid"] == proc.pid
    proc.wait(timeout=5)
    assert proc.poll() is not None
    assert run_control.read_control(str(tmp_path)) is None  # 정리됨


def test_control_status_shape(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, plans=("smoke", "comprehensive"))
    s = v.control_status()
    assert s["active"] is False and s["pid"] is None
    assert s["plans"] == ["smoke", "comprehensive"]
