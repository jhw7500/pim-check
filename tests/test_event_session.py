"""tests/test_event_session.py - EventSession 통합 헬퍼 검증.

EventSession 은 run_stream(파일/symlink) + event_stream(직렬화/내구성 기록)을
묶어 한 run 의 이벤트 수명주기를 관리한다. 파일 핸들 소유, thread-safe emit,
heartbeat 백그라운드 스레드를 책임진다.
"""
from __future__ import annotations

import json
import os
import time

from event_session import EventSession


def _read_lines(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


class TestLifecycleFiles:
    def test_enter_creates_run_file_and_current_symlink(self, tmp_path):
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "smoke", "board-A", events_dir=events_dir) as sess:
            assert os.path.exists(sess.run_path)
            current = os.path.join(events_dir, "current.jsonl")
            assert os.path.islink(current)
            # current.jsonl 은 run 파일을 가리킨다.
            assert os.path.realpath(current) == os.path.realpath(sess.run_path)

    def test_emit_is_immediately_readable(self, tmp_path):
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "smoke", "board-A", events_dir=events_dir) as sess:
            sess.emit_run_start(cases=["a", "b"])
            # 핸들을 닫기 전에 별도 핸들에서 즉시 읽혀야 한다 (flush+fsync).
            recs = _read_lines(sess.run_path)
            assert len(recs) == 1
            assert recs[0]["event_type"] == "run_start"
            assert recs[0]["cases"] == ["a", "b"]
            assert recs[0]["total_cases"] == 2


class TestElapsed:
    def test_elapsed_uses_injected_clock(self, tmp_path):
        ticks = iter([100.0, 103.5])  # enter sets start, elapsed_s reads next
        with EventSession("r1", "p", "b", events_dir=str(tmp_path / "e"),
                          clock=lambda: next(ticks)) as sess:
            assert sess.elapsed_s() == 3.5


class TestEmitHelpers:
    def test_case_end_records_counts_and_reason(self, tmp_path):
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "comprehensive", "board-A", events_dir=events_dir) as sess:
            sess.emit_run_start(cases=["c1", "c2"])
            sess.emit_case_start("c1", "collect")
            sess.emit_case_end("c1", "validate", "pass", completed_cases=1,
                               pass_count=1, fail_count=0, avg_case_duration_s=2.0)
            sess.emit_case_end("c2", "validate", "fail", completed_cases=2,
                               pass_count=1, fail_count=1, avg_case_duration_s=2.5,
                               reason="카메라 끊김")
            sess.emit_run_end(completed_cases=2, pass_count=1, fail_count=1)
        recs = _read_lines(sess.run_path)
        kinds = [r["event_type"] for r in recs]
        assert kinds == ["run_start", "case_start", "case_end", "case_end", "run_end"]
        fail = [r for r in recs if r["event_type"] == "case_end" and r["result"] == "fail"][0]
        assert fail["reason"] == "카메라 끊김"
        assert recs[-1]["fail_count"] == 1


class TestHeartbeat:
    def test_heartbeat_thread_emits_increasing_seq(self, tmp_path):
        events_dir = str(tmp_path / "events")

        def _hbs(path):
            # 스레드가 동시에 기록 중이라 파싱 불가한 말미 라인은 건너뛴다.
            out = []
            try:
                with open(path, encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            rec = json.loads(ln)
                        except ValueError:
                            continue
                        if rec.get("event_type") == "heartbeat":
                            out.append(rec)
            except OSError:
                pass
            return out

        with EventSession("r1", "p", "b", events_dir=events_dir,
                          heartbeat_interval=0.05) as sess:
            sess.start_heartbeat()
            # heartbeat 마다 fsync 가 있어 느린 CI 러너에서는 고정 sleep 이 부족할
            # 수 있다(flake). 고정 sleep 대신 조건(>=2) 충족까지 넉넉히 폴링한다.
            deadline = time.time() + 5.0
            while time.time() < deadline and len(_hbs(sess.run_path)) < 2:
                time.sleep(0.02)
        hbs = _hbs(sess.run_path)
        assert len(hbs) >= 2
        seqs = [r["heartbeat_seq"] for r in hbs]
        assert seqs == sorted(seqs)  # 단조 증가
        assert seqs[0] >= 1

    def test_no_heartbeat_after_exit(self, tmp_path):
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "p", "b", events_dir=events_dir,
                          heartbeat_interval=0.05) as sess:
            sess.start_heartbeat()
            time.sleep(0.12)
        n_after_exit = len([r for r in _read_lines(sess.run_path)
                            if r["event_type"] == "heartbeat"])
        time.sleep(0.15)
        # exit 이후에는 heartbeat 가 더 늘지 않아야 한다 (스레드 종료).
        n_later = len([r for r in _read_lines(sess.run_path)
                       if r["event_type"] == "heartbeat"])
        assert n_later == n_after_exit


class TestHostRouting:
    """host 지정 시 EventSession 이 per-target 디렉터리로 라우팅한다.

    backward compat: host 미지정(=None)은 기존 events_dir 직속 동작 유지.
    multi-target 시: events_dir/by-target/<slug>/ 하위에 run 파일 생성 + per-host
    current.jsonl + legacy events_dir/current.jsonl 동시 갱신 + active.json 등록.
    """

    def test_no_host_keeps_legacy_layout(self, tmp_path):
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "p", "b", events_dir=events_dir) as sess:
            assert os.path.dirname(sess.run_path) == events_dir
            assert not os.path.exists(os.path.join(events_dir, "by-target"))
            assert not os.path.exists(os.path.join(events_dir, "active.json"))

    def test_host_routes_run_path_into_by_target_subdir(self, tmp_path):
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "smoke", "192.168.0.5",
                          events_dir=events_dir, host="192.168.0.5") as sess:
            expected_dir = os.path.join(events_dir, "by-target", "192-168-0-5")
            assert os.path.dirname(sess.run_path) == expected_dir
            # emit 도 per-target 파일에 쓰여야 한다.
            sess.emit_run_start(cases=["c1"])
        recs = _read_lines(sess.run_path)
        assert any(r["event_type"] == "run_start" for r in recs)

    def test_host_updates_legacy_current_symlink_for_tui(self, tmp_path):
        # TUI viewer 호환 — legacy events/current.jsonl 도 갱신돼야 한다.
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "p", "host-a",
                          events_dir=events_dir, host="host-a") as sess:
            legacy = os.path.join(events_dir, "current.jsonl")
            assert os.path.islink(legacy)
            assert os.path.realpath(legacy) == os.path.realpath(sess.run_path)

    def test_host_registers_in_active_hosts(self, tmp_path):
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "smoke", "host-a",
                          events_dir=events_dir, host="host-a"):
            active_path = os.path.join(events_dir, "active.json")
            assert os.path.exists(active_path)
            with open(active_path) as f:
                data = json.load(f)
            hosts = [h["host"] for h in data["hosts"]]
            assert "host-a" in hosts


class TestThreadSafety:
    def test_concurrent_emit_lines_stay_valid_json(self, tmp_path):
        events_dir = str(tmp_path / "events")
        with EventSession("r1", "p", "b", events_dir=events_dir,
                          heartbeat_interval=0.01) as sess:
            sess.start_heartbeat()
            for i in range(50):
                sess.emit_case_start(f"c{i}", "collect")
        # 모든 라인이 손상 없이 파싱되어야 한다 (lock 으로 interleaving 방지).
        recs = _read_lines(sess.run_path)
        assert len([r for r in recs if r["event_type"] == "case_start"]) == 50
