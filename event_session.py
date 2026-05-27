"""event_session.py — 한 pim_check.py 실행의 JSONL 이벤트 수명주기 관리.

run_stream(run-scoped 파일 + current.jsonl symlink)과 event_stream(직렬화 +
flush/fsync 내구성 기록)을 하나로 묶는 얇은 오케스트레이터다. 책임은 셋:

1. run 시작 시 run-scoped 파일 생성 + current.jsonl symlink 갱신, append 핸들 소유.
2. thread-safe emit (메인 실행 흐름과 heartbeat 스레드가 같은 핸들에 쓰므로 lock).
3. heartbeat 백그라운드 스레드 (기본 5초, 설정 가능) — producer 생존 신호.

emit_* 헬퍼는 elapsed_s 를 자동으로 채워 event_stream 직렬화기에 위임한다.
컨텍스트 매니저로 쓰면 종료 시 heartbeat 정지 + 핸들 close 가 보장된다.
"""
from __future__ import annotations

import threading
import time

import event_stream as es
import run_stream

DEFAULT_HEARTBEAT_INTERVAL = 5.0


class EventSession:
    def __init__(self, run_id: str, plan: str, board: str, *,
                 events_dir: str | None = None,
                 host: str | None = None,
                 heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
                 clock=None) -> None:
        self.run_id = run_id
        self.plan = plan
        self.board = board
        self.events_dir = events_dir
        # multi-target 라우팅 — None 이면 기존(events/ 직속) 동작, 값이 있으면
        # events/by-target/<slug>/ 로 라우팅 + active.json 등록 + legacy current 갱신.
        self.host = host
        self.heartbeat_interval = heartbeat_interval
        self._clock = clock if clock is not None else time.monotonic
        self.run_path: str | None = None
        self._handle = None
        self._start: float | None = None
        self._lock = threading.Lock()
        self._hb_seq = 0
        self._hb_thread: threading.Thread | None = None
        self._stop = threading.Event()

    # --- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "EventSession":
        self.run_path = run_stream.start_run_file(
            self.plan, self.board, events_dir=self.events_dir, host=self.host,
        )
        self._handle = open(self.run_path, "a", encoding="utf-8")
        self._start = self._clock()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._hb_thread is not None:
            self._hb_thread.join(timeout=self.heartbeat_interval + 1.0)
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def elapsed_s(self) -> float:
        """run 시작 이후 경과 초 (단조 시계 기준)."""
        if self._start is None:
            return 0.0
        return self._clock() - self._start

    # --- emit --------------------------------------------------------------
    def emit(self, line: str) -> None:
        """직렬화된 한 줄을 thread-safe 하게 append + flush + fsync 한다."""
        with self._lock:
            es.write_event(self._handle, line)

    def _common(self) -> dict:
        return {"run_id": self.run_id, "plan": self.plan, "board": self.board}

    def emit_run_start(self, cases, total_cases=None, case_plans=None) -> None:
        self.emit(es.serialize_run_start(
            **self._common(), elapsed_s=self.elapsed_s(),
            cases=cases, total_cases=total_cases, case_plans=case_plans,
        ))

    def emit_case_start(self, case_name: str, phase: str,
                        case_desc=None, checklist=None) -> None:
        self.emit(es.serialize_case_start(
            **self._common(), elapsed_s=self.elapsed_s(),
            case_name=case_name, phase=phase,
            case_desc=case_desc, checklist=checklist,
        ))

    def emit_case_end(self, case_name: str, phase: str, result: str, *,
                      completed_cases: int, pass_count: int, fail_count: int,
                      avg_case_duration_s: float, reason: str | None = None,
                      checklist_results: list | None = None) -> None:
        self.emit(es.serialize_case_end(
            **self._common(), elapsed_s=self.elapsed_s(),
            case_name=case_name, phase=phase, result=result,
            completed_cases=completed_cases, pass_count=pass_count,
            fail_count=fail_count, avg_case_duration_s=avg_case_duration_s,
            reason=reason, checklist_results=checklist_results,
        ))

    def emit_run_end(self, *, completed_cases: int, pass_count: int,
                     fail_count: int) -> None:
        self.emit(es.serialize_run_end(
            **self._common(), elapsed_s=self.elapsed_s(),
            completed_cases=completed_cases, pass_count=pass_count,
            fail_count=fail_count,
        ))

    def emit_fail(self, check: str, reason: str, **context) -> None:
        """체크 단위 fail 이벤트 (base_check.validate_and_emit 의 emitter 로 사용)."""
        self.emit(es.serialize_fail_event(check, reason, **context))

    # --- heartbeat ---------------------------------------------------------
    def start_heartbeat(self) -> None:
        """heartbeat 백그라운드 스레드를 시작한다 (idempotent)."""
        if self._hb_thread is not None:
            return
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="pim-heartbeat", daemon=True,
        )
        self._hb_thread.start()

    def _heartbeat_loop(self) -> None:
        # Event.wait 는 stop 이 set 되면 즉시 True 를 돌려주므로 종료가 빠르다.
        while not self._stop.wait(self.heartbeat_interval):
            self._hb_seq += 1
            self.emit(es.serialize_heartbeat(
                **self._common(), elapsed_s=self.elapsed_s(),
                heartbeat_seq=self._hb_seq,
            ))
