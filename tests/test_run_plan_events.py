"""tests/test_run_plan_events.py - _run_plan 이벤트 스트림 와이어링 통합 테스트.

plan.* 의존을 fake 로 치환하고 run_stream 의 events 디렉토리를 tmp 로 돌려,
_run_plan 이 한 plan 실행에 대해 run_start → case_start/case_end → run_end
이벤트를 events/current.jsonl 에 올바른 순서/카운트로 기록하는지 검증한다.
(SSH/엔진 없이, 네트워크 없이.)
"""
from __future__ import annotations

import argparse
import json
import os
import types

import pim_check
import plan as plan_mod
import run_stream


class _Exec:
    def __init__(self, section, case_name, passed, results, error=None, duration_sec=1.0):
        self.section = section
        self.case_name = case_name
        self.passed = passed
        self.results = results
        self.error = error
        self.retries_used = 0
        self.duration_sec = duration_sec


class _Gate:
    verdict = "FAIL"
    pass_rate = 0.5
    regressions: list = []
    fixed: list = []
    new_cases: list = []
    known_warns: list = []


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_run_plan_emits_full_event_stream(tmp_path, monkeypatch):
    events_dir = str(tmp_path / "events")
    monkeypatch.setattr(run_stream, "default_events_dir", lambda: events_dir)

    fake_plan = types.SimpleNamespace(
        name="smoke", description="d",
        execution={"stop_on_fail": False, "case_retry": 0},
        gate={},
    )
    monkeypatch.setattr(plan_mod, "load_plan", lambda path: fake_plan)
    monkeypatch.setattr(plan_mod, "load_baseline", lambda ref, root: (None, None))
    monkeypatch.setattr(plan_mod, "evaluate_gate", lambda *a, **k: _Gate())
    monkeypatch.setattr(plan_mod, "render_reports", lambda *a, **k: [])
    monkeypatch.setattr(
        plan_mod, "resolve_cases",
        lambda plan, profiles_dir: [("regression", "c1"), ("regression", "c2")],
    )

    def fake_execute_plan(plan, profiles_dir, *, ssh_factory, setup_factory,
                          engine_factory, cli_args, progress, on_case_start):
        execs = []
        cases = [
            _Exec("regression", "c1", True,
                  [{"name": "cpu", "passed": True, "reason": "OK"}]),
            _Exec("regression", "c2", False,
                  [{"name": "process", "passed": False, "reason": "gstApp 죽음"}]),
        ]
        total = len(cases)
        for idx, ex in enumerate(cases, 1):
            on_case_start(idx, total, ex.case_name, ex.section)
            progress(idx, total, ex.case_name, ex)
            execs.append(ex)
        return execs

    monkeypatch.setattr(plan_mod, "execute_plan", fake_execute_plan)

    args = argparse.Namespace(
        plan="smoke", host="192.168.0.5", user=None, password=None,
        duration=None, quiet=True,
    )
    rc = pim_check._run_plan(args)
    assert rc == 1  # FAIL verdict

    recs = _read(os.path.join(events_dir, "current.jsonl"))
    kinds = [r["event_type"] for r in recs if r["event_type"] != "heartbeat"]
    assert kinds == ["run_start", "case_start", "case_end",
                     "case_start", "case_end", "run_end"]

    run_start = recs[0]
    assert run_start["cases"] == ["c1", "c2"]
    assert run_start["total_cases"] == 2
    assert run_start["plan"] == "smoke"

    case_ends = [r for r in recs if r["event_type"] == "case_end"]
    assert case_ends[0]["result"] == "pass"
    assert "reason" not in case_ends[0]
    assert case_ends[1]["result"] == "fail"
    assert case_ends[1]["reason"] == "gstApp 죽음"
    assert case_ends[1]["pass_count"] == 1
    assert case_ends[1]["fail_count"] == 1
    assert case_ends[1]["completed_cases"] == 2

    run_end = recs[-1]
    assert run_end["event_type"] == "run_end"
    assert run_end["pass_count"] == 1
    assert run_end["fail_count"] == 1
    assert run_end["completed_cases"] == 2


def test_run_plan_runs_even_if_event_layer_fails(tmp_path, monkeypatch):
    # EventSession 생성이 실패해도 plan 실행/리턴은 정상이어야 한다 (best-effort).
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(run_stream, "start_run_file", boom)

    fake_plan = types.SimpleNamespace(
        name="smoke", description="d",
        execution={"stop_on_fail": False, "case_retry": 0}, gate={},
    )
    monkeypatch.setattr(plan_mod, "load_plan", lambda path: fake_plan)
    monkeypatch.setattr(plan_mod, "load_baseline", lambda ref, root: (None, None))
    monkeypatch.setattr(plan_mod, "evaluate_gate", lambda *a, **k: _Gate())
    monkeypatch.setattr(plan_mod, "render_reports", lambda *a, **k: [])
    monkeypatch.setattr(plan_mod, "resolve_cases",
                        lambda plan, profiles_dir: [("regression", "c1")])

    def fake_execute_plan(plan, profiles_dir, *, ssh_factory, setup_factory,
                          engine_factory, cli_args, progress, on_case_start):
        ex = _Exec("regression", "c1", True,
                   [{"name": "cpu", "passed": True, "reason": "OK"}])
        on_case_start(1, 1, "c1", "regression")
        progress(1, 1, "c1", ex)
        return [ex]
    monkeypatch.setattr(plan_mod, "execute_plan", fake_execute_plan)

    args = argparse.Namespace(plan="smoke", host="192.168.0.5", user=None,
                              password=None, duration=None, quiet=True)
    # 이벤트 레이어가 죽어도 예외 없이 verdict exit code 를 돌려준다.
    rc = pim_check._run_plan(args)
    assert rc == 1
