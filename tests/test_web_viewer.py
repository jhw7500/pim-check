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
