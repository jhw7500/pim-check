#!/usr/bin/env python3
"""pim_viewer — pim_check.py 실시간 진행/Fault 관측 TUI.

기본적으로 ``events/current.jsonl`` (run_stream 이 run 시작 시 atomic 하게 갱신하는
symlink)을 tail 한다. 시작 시 JSONL 을 처음부터 한 번 훑어 (monotonic replay)
현재 상태를 복원하므로 SSH 재접속 후 다시 띄워도 "끊긴 적 없는 것처럼" 보인다.
이후 새 라인을 따라가며, 마지막 이벤트(heartbeat 포함) 이후 일정 시간 무응답이면
"Producer lost" 를 표시한다.

format_dashboard() 는 rich 의존이 없는 순수 문자열 렌더러라 단위 테스트가 쉽다.
라이브 표시는 rich 가 있으면 rich.Live, 없으면 화면 clear + print 로 폴백한다.
"""
from __future__ import annotations

import argparse
import os
import time

import run_stream
from viewer_state import ViewerState

# 2x 기본 heartbeat 간격(5s) — 설정 가능. 이 시간 동안 새 이벤트가 없으면 producer 사망 추정.
PRODUCER_LOST_AFTER = 10.0

_MARK = {"pass": "✓", "fail": "✗", "running": "⏳", "pending": "⏳"}


def _fmt_eta(seconds: float) -> str:
    s = int(round(max(seconds, 0.0)))
    if s < 60:
        return f"~{s}s"
    return f"~{s // 60}m {s % 60}s"


def format_dashboard(state: ViewerState, *, producer_lost: bool = False) -> str:
    """ViewerState → 사람이 읽는 대시보드 문자열 (순수 함수, rich 불필요)."""
    out: list[str] = []
    title = f"pim_viewer — plan={state.plan or '?'} board={state.board or '?'}"
    if state.run_id:
        title += f" run={state.run_id}"
    out.append(title)

    if producer_lost:
        out.append("❌ Producer lost — no events recently (producer may have stopped)")
    elif state.run_ended:
        out.append("● DONE")
    else:
        out.append("● RUNNING")

    done, total = state.progress
    pct = int(round(100 * done / total)) if total else 0
    out.append(f"Progress: {done}/{total} ({pct}%)")
    out.append(f"Pass: {state.pass_count}   Fail: {state.fail_count}")
    out.append(f"Current: {state.current_case or '—'}")
    out.append(f"ETA: {_fmt_eta(state.eta_seconds)}")

    cls = state.fail_classification
    if cls:
        summ = state.fail_summaries
        confirmed = [n for n in cls if cls[n] == "confirmed"]
        active = [n for n in cls if cls[n] == "active"]
        resolved = [n for n in cls if cls[n] == "resolved"]
        if confirmed:
            out.append("Failures (final):")
            for name in confirmed:
                out.append(f"  ✗ {name}: {summ.get(name, '')}")
        if active:
            out.append("Faults (in progress):")
            for name in active:
                out.append(f"  ⚠ {name}: {summ.get(name, '')}")
        if resolved:
            out.append("Recovered after retry (transient):")
            for name in resolved:
                out.append(f"  ↻ {name}")

    out.append("Cases:")
    for name in state.cases:
        marker = _MARK.get(state.case_status.get(name, "pending"), "⏳")
        cur = "  ◀ running" if name == state.current_case else ""
        out.append(f"  {marker} {name}{cur}")

    return "\n".join(out)


def _default_path() -> str:
    return os.path.join(run_stream.default_events_dir(), run_stream.CURRENT_SYMLINK_NAME)


class _Tailer:
    """current.jsonl 을 따라가며 상태를 갱신한다. symlink 가 새 run 으로 repoint
    되거나 파일이 truncate 되면 (inode 변화/크기 감소) 상태를 리셋하고 처음부터
    다시 읽는다."""

    def __init__(self, path: str):
        self.path = path
        self.state = ViewerState()
        self._offset = 0
        self._inode = None
        self._buf = ""
        self.last_event_mono = time.monotonic()

    def poll(self) -> bool:
        """새 라인을 읽어 상태에 반영. 새 이벤트가 있었으면 True."""
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        # 새 run 으로 symlink 가 바뀌었거나 파일이 줄었으면 처음부터 다시.
        if self._inode is not None and (st.st_ino != self._inode or st.st_size < self._offset):
            self.state = ViewerState()
            self._offset = 0
            self._buf = ""
        self._inode = st.st_ino
        if st.st_size <= self._offset:
            return False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except OSError:
            return False
        if not chunk:
            return False
        self._buf += chunk
        got = False
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            import json
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            self.state.apply(ev)
            got = True
        if got:
            self.last_event_mono = time.monotonic()
        return got

    def producer_lost(self, threshold: float) -> bool:
        if self.state.run_ended:
            return False
        return (time.monotonic() - self.last_event_mono) > threshold


def _render_live(tailer: _Tailer, interval: float, threshold: float) -> None:
    """rich 가 있으면 rich.Live, 없으면 clear+print 폴백으로 라이브 표시."""
    try:
        from rich.live import Live
        from rich.panel import Panel
        from rich.text import Text
    except ImportError:
        _render_live_plain(tailer, interval, threshold)
        return

    with Live(auto_refresh=False, screen=False) as live:
        while True:
            tailer.poll()
            lost = tailer.producer_lost(threshold)
            body = format_dashboard(tailer.state, producer_lost=lost)
            live.update(Panel(Text(body), title="pim_viewer"), refresh=True)
            time.sleep(interval)


def _render_live_plain(tailer: _Tailer, interval: float, threshold: float) -> None:
    while True:
        tailer.poll()
        lost = tailer.producer_lost(threshold)
        os.system("clear" if os.name != "nt" else "cls")
        print(format_dashboard(tailer.state, producer_lost=lost))
        time.sleep(interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="pim_viewer",
        description="pim_check.py 실시간 진행/Fault 관측 TUI (events/current.jsonl tail).",
    )
    ap.add_argument("path", nargs="?", default=None,
                    help="JSONL 경로 (기본: events/current.jsonl)")
    ap.add_argument("--interval", type=float, default=0.2,
                    help="폴링 주기 초 (기본 0.2)")
    ap.add_argument("--producer-lost-after", type=float, default=PRODUCER_LOST_AFTER,
                    help="이 시간(초) 동안 무응답이면 Producer lost 표시 (기본 10)")
    ap.add_argument("--once", action="store_true",
                    help="현재 상태를 한 번만 렌더하고 종료 (라이브 루프 없음)")
    args = ap.parse_args(argv)

    path = args.path or _default_path()
    tailer = _Tailer(path)

    if args.once:
        tailer.poll()
        lost = tailer.producer_lost(args.producer_lost_after)
        print(format_dashboard(tailer.state, producer_lost=lost))
        return 0

    if not os.path.exists(path):
        print(f"waiting for event stream at {path} ... (run pim_check.py --plan <name>)")
    try:
        _render_live(tailer, args.interval, args.producer_lost_after)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
