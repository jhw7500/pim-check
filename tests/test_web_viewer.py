"""tests/test_web_viewer.py - pim_web_viewer.build_state 검증.

웹 뷰어는 TUI 와 동일한 viewer_state.ViewerState 를 재사용해 같은 JSONL 을
JSON 으로 노출한다. build_state 는 매 요청마다 파일을 처음부터 접어 현재 상태 +
producer-lost(파일 mtime 기반)를 평면 dict 로 만든다 (순수, 테스트 대상).
"""
from __future__ import annotations

import json
import os
import time

from pim_web_viewer import build_state


def _write(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def test_missing_file_returns_not_exists(tmp_path):
    assert build_state(str(tmp_path / "none.jsonl")) == {"exists": False}


def test_reconstructs_state_from_jsonl(tmp_path):
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "smoke", "board": "board-A",
         "elapsed_s": 0, "cases": ["c1", "c2"], "total_cases": 2},
        {"event_type": "case_end", "case_name": "c1", "phase": "validate",
         "result": "pass", "completed_cases": 1, "pass_count": 1, "fail_count": 0,
         "avg_case_duration_s": 2.0, "elapsed_s": 2},
        {"event_type": "case_start", "case_name": "c2", "phase": "collect",
         "elapsed_s": 2.1},
    ])
    s = build_state(p)
    assert s["exists"] is True
    assert s["plan"] == "smoke" and s["board"] == "board-A"
    assert s["total"] == 2 and s["completed"] == 1
    assert s["pass"] == 1 and s["fail"] == 0
    assert s["current"] == "c2"
    assert s["status"] == {"c1": "pass", "c2": "running"}
    assert s["producer_lost"] is False  # 방금 기록 → mtime 최신


def test_fail_summary_exposed(tmp_path):
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "p", "board": "b", "elapsed_s": 0,
         "cases": ["c1"], "total_cases": 1},
        {"event_type": "case_end", "case_name": "c1", "phase": "validate",
         "result": "fail", "completed_cases": 1, "pass_count": 0, "fail_count": 1,
         "avg_case_duration_s": 3.0, "reason": "gstApp 죽음", "elapsed_s": 3},
    ])
    s = build_state(p)
    assert s["fail"] == 1
    assert s["fail_summaries"] == {"c1": "gstApp 죽음"}


def test_producer_lost_when_file_stale(tmp_path):
    p = str(tmp_path / "e.jsonl")
    _write(p, [{"event_type": "run_start", "plan": "p", "board": "b",
                "elapsed_s": 0, "cases": ["c1"], "total_cases": 1}])
    old = time.time() - 30  # 10초 임계 초과
    os.utime(p, (old, old))
    s = build_state(p)
    assert s["producer_lost"] is True


def test_no_producer_lost_after_run_end(tmp_path):
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "p", "board": "b", "elapsed_s": 0,
         "cases": ["c1"], "total_cases": 1},
        {"event_type": "run_end", "completed_cases": 1, "pass_count": 1,
         "fail_count": 0, "elapsed_s": 5},
    ])
    old = time.time() - 60
    os.utime(p, (old, old))
    s = build_state(p)
    # 정상 종료(run_end) 후에는 무응답이어도 producer-lost 아님.
    assert s["run_ended"] is True
    assert s["producer_lost"] is False


def test_pending_exposed_and_not_a_fault(tmp_path):
    # 현재 케이스가 '준비 중'(NEED_2_FINALIZES)이면 pending 으로 노출, fault 아님.
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "smoke", "board": "b", "elapsed_s": 0,
         "cases": ["c1"], "total_cases": 1},
        {"event_type": "case_start", "case_name": "c1", "phase": "collect", "elapsed_s": 0.1},
        {"event_type": "pending", "check": "recording", "reason": "NEED_2_FINALIZES",
         "case_name": "c1", "elapsed_s": 1},
    ])
    s = build_state(p)
    assert s["pending"] == "NEED_2_FINALIZES"
    assert s["fail_classification"] == {}
    assert s["fail"] == 0


def test_case_checklist_exposed_in_detail(tmp_path):
    # case_start 의 설명 + 체크리스트가 case_detail 에 노출되고 결과로 상태 유도.
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "smoke", "board": "b", "elapsed_s": 0,
         "cases": ["c1"], "total_cases": 1},
        {"event_type": "case_start", "case_name": "c1", "phase": "collect", "elapsed_s": 0.1,
         "case_desc": "설명", "checklist": [
             {"name": "fps", "command": "cmd1", "expected": "OK"},
             {"name": "bps", "command": "cmd2", "expected": "OK"}]},
        {"event_type": "case_end", "case_name": "c1", "phase": "validate", "result": "fail",
         "completed_cases": 1, "pass_count": 0, "fail_count": 1, "avg_case_duration_s": 5.0,
         "reason": "fps: mismatch (got: 30)", "elapsed_s": 5},
    ])
    cd = build_state(p)["case_detail"]["c1"]
    assert cd["desc"] == "설명"
    assert cd["checks_total"] == 2 and cd["checks_passed"] == 1
    assert {i["name"]: i["status"] for i in cd["checklist"]} == {"fps": "fail", "bps": "pass"}
    assert cd["checklist"][0]["command"] == "cmd1"


def test_checklist_actual_values_exposed_in_detail(tmp_path):
    # case_end 의 checklist_results 가 case_detail 체크리스트에 실측값으로 노출되고
    # per-item passed 가 항목 상태의 권위가 된다.
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "smoke", "board": "b", "elapsed_s": 0,
         "cases": ["c1"], "total_cases": 1},
        {"event_type": "case_start", "case_name": "c1", "phase": "collect", "elapsed_s": 0.1,
         "checklist": [
             {"name": "ROT", "command": "i2c", "expected": "0x000x02"},
             {"name": "bps", "command": "ffprobe", "expected": ">= 8000"}]},
        {"event_type": "case_end", "case_name": "c1", "phase": "validate", "result": "fail",
         "completed_cases": 1, "pass_count": 0, "fail_count": 1, "avg_case_duration_s": 5.0,
         "reason": "bps low", "elapsed_s": 5,
         "checklist_results": [
             {"name": "ROT", "actual": "0x000x02", "passed": True},
             {"name": "bps", "actual": "5596", "passed": False}]},
    ])
    cd = build_state(p)["case_detail"]["c1"]
    by = {i["name"]: i for i in cd["checklist"]}
    assert by["ROT"]["actual"] == "0x000x02" and by["ROT"]["status"] == "pass"
    assert by["bps"]["actual"] == "5596" and by["bps"]["status"] == "fail"
    assert cd["checks_passed"] == 1


def test_elapsed_s_exposed_for_live_clock(tmp_path):
    # 웹 라이브 경과 시계용: build_state 가 run elapsed_s 를 노출한다.
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "smoke", "board": "b", "elapsed_s": 0,
         "cases": ["c1"], "total_cases": 1},
        {"event_type": "heartbeat", "elapsed_s": 42.5, "heartbeat_seq": 8},
    ])
    s = build_state(p)
    assert s["elapsed_s"] == 42.5


def test_transient_fail_classified_resolved(tmp_path):
    # 도중 check fail 이벤트가 떴지만 case 는 pass → resolved(일시 fail).
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "smoke", "board": "b", "elapsed_s": 0,
         "cases": ["720p_4ch"], "total_cases": 1},
        {"event_type": "case_start", "case_name": "720p_4ch", "phase": "collect",
         "elapsed_s": 0.1},
        {"event_type": "fail", "check": "recording", "reason": "NEED_2_FINALIZES",
         "case_name": "720p_4ch", "elapsed_s": 114},
        {"event_type": "case_end", "case_name": "720p_4ch", "phase": "validate",
         "result": "pass", "completed_cases": 1, "pass_count": 1, "fail_count": 0,
         "avg_case_duration_s": 567.0, "elapsed_s": 567},
    ])
    s = build_state(p)
    # 최종 카운트는 pass — 빨간 fail 아님.
    assert s["fail"] == 0 and s["pass"] == 1
    assert s["fail_classification"] == {"720p_4ch": "resolved"}
    # 드릴다운 상세에 fault 이벤트가 보존된다.
    det = s["case_detail"]["720p_4ch"]
    assert det["classification"] == "resolved"
    assert det["fail_count"] == 1
    assert det["fails"][0]["check"] == "recording"
    assert det["duration_s"] == 566.9


def test_confirmed_vs_resolved_separated(tmp_path):
    p = str(tmp_path / "e.jsonl")
    _write(p, [
        {"event_type": "run_start", "plan": "p", "board": "b", "elapsed_s": 0,
         "cases": ["c1", "c2"], "total_cases": 2},
        {"event_type": "case_start", "case_name": "c1", "phase": "collect", "elapsed_s": 0.1},
        {"event_type": "fail", "check": "recording", "reason": "transient",
         "case_name": "c1", "elapsed_s": 1},
        {"event_type": "case_end", "case_name": "c1", "phase": "validate",
         "result": "pass", "completed_cases": 1, "pass_count": 1, "fail_count": 0,
         "avg_case_duration_s": 5.0, "elapsed_s": 5},
        {"event_type": "case_start", "case_name": "c2", "phase": "collect", "elapsed_s": 5.1},
        {"event_type": "case_end", "case_name": "c2", "phase": "validate",
         "result": "fail", "completed_cases": 2, "pass_count": 1, "fail_count": 1,
         "avg_case_duration_s": 5.0, "reason": "real fail", "elapsed_s": 10},
    ])
    s = build_state(p)
    assert s["fail_classification"] == {"c1": "resolved", "c2": "confirmed"}
    assert s["case_detail"]["c2"]["status"] == "fail"


# ---- _check_paramiko_available (silent sshpass 폴백 차단 검증) -------------------------

def test_check_paramiko_available_returns_true_when_present(capsys):
    """paramiko 가 환경에 있으면 True + stderr silent."""
    import importlib
    try:
        importlib.import_module("paramiko")
    except ImportError:  # pragma: no cover — pyproject 의존성이라 거의 항상 있음
        import pytest
        pytest.skip("paramiko unavailable in test env")
    from pim_web_viewer import _check_paramiko_available
    assert _check_paramiko_available() is True
    assert capsys.readouterr().err == ""


def test_check_paramiko_available_warns_when_missing(monkeypatch, capsys):
    """paramiko import 실패 시 False + stderr 에 paramiko/sshpass 안내 출력.

    sys.modules 에 ``None`` 을 박으면 다음 `import paramiko` 가 ImportError 발생
    (Python 3.5+ 의 documented 동작). 함수 안 import 가 그 ImportError 를 잡아
    경고 출력 + False 반환하는지 검증.
    """
    import sys
    from pim_web_viewer import _check_paramiko_available
    monkeypatch.setitem(sys.modules, "paramiko", None)
    assert _check_paramiko_available() is False
    err = capsys.readouterr().err
    assert "paramiko" in err
    assert "sshpass" in err.lower()
