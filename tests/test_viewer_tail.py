"""tests/test_viewer_tail.py - pim_viewer._Tailer 증분 tail + 복원 + liveness.

EventSession 이 만든 실제 current.jsonl 을 _Tailer 로 따라가며: 재접속 시 처음부터
복원(replay), 증분 갱신, Producer-lost 판정, 새 run 으로 symlink repoint 시 상태
리셋을 검증한다.
"""
from __future__ import annotations

import json
import os

import run_stream
from event_session import EventSession
from pim_viewer import _Tailer


def test_tailer_replays_then_tails_incrementally(tmp_path):
    events_dir = str(tmp_path / "events")
    with EventSession("r1", "comprehensive", "board-A", events_dir=events_dir) as sess:
        sess.emit_run_start(cases=["c1", "c2"])
        sess.emit_case_start("c1", "collect")
        sess.emit_case_end("c1", "validate", "pass", completed_cases=1,
                           pass_count=1, fail_count=0, avg_case_duration_s=2.0)

        current = os.path.join(events_dir, "current.jsonl")
        tail = _Tailer(current)
        # 재접속: 처음부터 복원.
        assert tail.poll() is True
        assert tail.state.progress == (1, 2)
        assert tail.state.case_status["c1"] == "pass"

        # 증분: 새 이벤트가 반영된다.
        sess.emit_case_start("c2", "collect")
        assert tail.poll() is True
        assert tail.state.current_case == "c2"

        # 새 이벤트 없으면 False.
        assert tail.poll() is False


def test_producer_lost_after_threshold_but_not_after_run_end(tmp_path):
    events_dir = str(tmp_path / "events")
    with EventSession("r1", "p", "b", events_dir=events_dir) as sess:
        sess.emit_run_start(cases=["c1"])
        current = os.path.join(events_dir, "current.jsonl")
        tail = _Tailer(current)
        tail.poll()
        # 방금 이벤트를 받았으니 lost 아님.
        assert tail.producer_lost(threshold=1000) is False
        # 마지막 이벤트 시각을 과거로 밀어 무응답을 모사.
        tail.last_event_mono -= 20
        assert tail.producer_lost(threshold=10) is True
        # run_end 후에는 무응답이어도 lost 아님 (정상 종료).
        sess.emit_run_end(completed_cases=1, pass_count=1, fail_count=0)
        tail.poll()
        tail.last_event_mono -= 20
        assert tail.producer_lost(threshold=10) is False


def test_tailer_resets_when_symlink_repoints_to_new_run(tmp_path):
    events_dir = str(tmp_path / "events")
    os.makedirs(events_dir)

    p1 = run_stream.start_run_file("p", "b", events_dir=events_dir, ts="20260520T000001")
    with open(p1, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event_type": "run_start", "run_id": "r1", "plan": "p",
                            "board": "b", "elapsed_s": 0.0,
                            "cases": ["a", "b", "c"], "total_cases": 3}) + "\n")

    current = os.path.join(events_dir, "current.jsonl")
    tail = _Tailer(current)
    tail.poll()
    assert tail.state.total_cases == 3

    # 새 run: 새 run 파일 + current.jsonl repoint (다른 inode).
    p2 = run_stream.start_run_file("p", "b", events_dir=events_dir, ts="20260520T000002")
    with open(p2, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event_type": "run_start", "run_id": "r2", "plan": "p",
                            "board": "b", "elapsed_s": 0.0,
                            "cases": ["x"], "total_cases": 1}) + "\n")

    tail.poll()
    # 상태가 새 run 으로 리셋된다.
    assert tail.state.total_cases == 1
    assert tail.state.run_id == "r2"
