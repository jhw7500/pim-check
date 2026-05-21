"""viewer_state.py — PimEventStream JSONL 을 화면 상태로 접는 순수 모델.

pim_viewer 의 핵심. IO/렌더링과 분리된 순수 상태 머신이라, 재접속 시 JSONL 을
처음부터 한 번 훑어 (monotonic replay) 현재 상태를 복원하는 로직을 단위 테스트할
수 있다. case_end / run_end 의 누적 카운트를 권위 값으로 신뢰하므로, 같은 이벤트
시퀀스는 항상 같은 최종 상태를 만든다.
"""
from __future__ import annotations

import json
from typing import Iterable


class ViewerState:
    def __init__(self) -> None:
        self.run_id: str | None = None
        self.plan: str | None = None
        self.board: str | None = None
        self.total_cases: int = 0
        self.completed_cases: int = 0
        self.pass_count: int = 0
        self.fail_count: int = 0
        self.current_case: str | None = None
        self.avg_case_duration_s: float = 0.0
        self.elapsed_s: float = 0.0
        self.last_heartbeat_seq: int = 0
        self.run_ended: bool = False
        self._cases: list[str] = []           # plan 순서 유지
        self._status: dict[str, str] = {}      # name -> pending/running/pass/fail
        self._fail_summaries: dict[str, str] = {}  # name -> reason (발생 순서)
        # 케이스별 상세(드릴다운 + 일시/최종 fail 구분)용 추가 상태.
        self._case_fails: dict[str, list[dict]] = {}   # name -> [{check,reason,ts,elapsed_s}]
        self._case_phase: dict[str, str] = {}          # name -> 마지막 phase
        self._case_started_s: dict[str, float] = {}    # name -> 첫 case_start elapsed_s
        self._case_ended_s: dict[str, float] = {}      # name -> 마지막 case_end elapsed_s
        self._case_pending: dict[str, str] = {}        # name -> 마지막 '준비 중' reason (fault 아님)

    # --- folding -----------------------------------------------------------
    def apply(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        et = event.get("event_type")
        es = event.get("elapsed_s")
        if isinstance(es, (int, float)):
            self.elapsed_s = max(self.elapsed_s, float(es))

        if et == "run_start":
            self.run_id = event.get("run_id")
            self.plan = event.get("plan")
            self.board = event.get("board")
            self._cases = list(event.get("cases") or [])
            self.total_cases = event.get("total_cases", len(self._cases))
            self._status = {c: "pending" for c in self._cases}
        elif et == "case_start":
            name = event.get("case_name")
            if name is not None:
                if name not in self._status:
                    self._cases.append(name)
                self._status[name] = "running"
                self.current_case = name
                phase = event.get("phase")
                if phase is not None:
                    self._case_phase[name] = phase
                if isinstance(es, (int, float)):
                    self._case_started_s.setdefault(name, float(es))
        elif et == "case_end":
            name = event.get("case_name")
            result = event.get("result")
            if name is not None:
                if name not in self._status:
                    self._cases.append(name)
                if result in ("pass", "fail"):
                    self._status[name] = result
                if result == "fail":
                    reason = event.get("reason")
                    self._fail_summaries[name] = reason if reason is not None else ""
                phase = event.get("phase")
                if phase is not None:
                    self._case_phase[name] = phase
                if isinstance(es, (int, float)):
                    self._case_ended_s[name] = float(es)
                # 케이스 종료 → 더 이상 '준비 중' 아님.
                self._case_pending.pop(name, None)
                if self.current_case == name:
                    self.current_case = None
            self.completed_cases = event.get("completed_cases", self.completed_cases)
            self.pass_count = event.get("pass_count", self.pass_count)
            self.fail_count = event.get("fail_count", self.fail_count)
            avg = event.get("avg_case_duration_s")
            if isinstance(avg, (int, float)):
                self.avg_case_duration_s = float(avg)
        elif et == "run_end":
            self.completed_cases = event.get("completed_cases", self.completed_cases)
            self.pass_count = event.get("pass_count", self.pass_count)
            self.fail_count = event.get("fail_count", self.fail_count)
            self.current_case = None
            self.run_ended = True
        elif et == "heartbeat":
            seq = event.get("heartbeat_seq")
            if isinstance(seq, int):
                self.last_heartbeat_seq = max(self.last_heartbeat_seq, seq)
        elif et == "fail":
            # 체크 단위 실시간 fail — case_end 전에 fault 를 즉시 표면화한다.
            # 카운트/상태는 case_end 가 권위이므로 여기선 reason 만 기록(첫 발생 우선).
            name = event.get("case_name")
            if name is not None:
                reason = event.get("reason")
                reason = reason if reason is not None else ""
                if name not in self._fail_summaries:
                    self._fail_summaries[name] = reason
                # 케이스별 전체 fail 이벤트 보존 — 드릴다운 + 일시/최종 분류에 사용.
                self._case_fails.setdefault(name, []).append({
                    "check": event.get("check"),
                    "reason": reason,
                    "ts": event.get("ts"),
                    "elapsed_s": event.get("elapsed_s"),
                })
                # 실제 fault 가 떴으면 더 이상 '준비 중' 아님 — pending 해제
                # (안 그러면 드릴다운이 ⚠ FAULT 와 ⏳ 준비 중 을 동시 표시).
                self._case_pending.pop(name, None)
        elif et == "pending":
            # 안정화 미달(준비 중) — fault 아님. 카운트/분류에 영향 주지 않고
            # 현재 케이스가 '준비 중'임을 표시하는 용도로만 마지막 reason 을 보존한다.
            name = event.get("case_name")
            if name is not None:
                self._case_pending[name] = event.get("reason") or ""
        # 그 외 알 수 없는 event_type 은 무시.

    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> "ViewerState":
        st = cls()
        for ln in lines:
            if isinstance(ln, str):
                ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except (ValueError, TypeError):
                continue
            st.apply(ev)
        return st

    # --- derived views -----------------------------------------------------
    @property
    def progress(self) -> tuple[int, int]:
        return (self.completed_cases, self.total_cases)

    @property
    def remaining(self) -> int:
        return max(self.total_cases - self.completed_cases, 0)

    @property
    def eta_seconds(self) -> float:
        """단순 추정: 평균 case 소요 × 남은 case 수 (±50% 허용, fallback 없음)."""
        return self.avg_case_duration_s * self.remaining

    @property
    def cases(self) -> list[str]:
        return list(self._cases)

    @property
    def case_status(self) -> dict[str, str]:
        return dict(self._status)

    @property
    def fail_summaries(self) -> dict[str, str]:
        return dict(self._fail_summaries)

    def _classify(self, name: str) -> str:
        """케이스의 fail 성격 분류.

        - confirmed: 최종 case_end 가 fail (진짜 실패)
        - resolved : case 는 pass 인데 도중 fail 이벤트가 있었음 (재시도로 회복된 일시 fail)
        - active   : 아직 running 인데 fail 이벤트가 떴음 (진행 중 fault, 결과 미정)
        - none     : fail 이벤트도 없고 최종 실패도 아님
        """
        status = self._status.get(name, "pending")
        if status == "fail":
            return "confirmed"
        if status == "pass":
            return "resolved" if self._case_fails.get(name) else "none"
        if status == "running":
            return "active" if self._case_fails.get(name) else "none"
        return "none"

    @property
    def fail_classification(self) -> dict[str, str]:
        """fail 이력이 있는 케이스 → 분류(confirmed/resolved/active)."""
        names = set(self._case_fails) | {
            n for n, s in self._status.items() if s == "fail"
        }
        return {n: self._classify(n) for n in names}

    @property
    def pending_summaries(self) -> dict[str, str]:
        """현재 running 중이면서 '준비 중'(stabilization 미달)인 케이스 → reason."""
        return {
            n: r for n, r in self._case_pending.items()
            if self._status.get(n) == "running"
        }

    @property
    def case_details(self) -> dict[str, dict]:
        """모든 케이스의 드릴다운 상세(상태/phase/소요시간/fail 목록/준비중)."""
        out: dict[str, dict] = {}
        for name in self._cases:
            start = self._case_started_s.get(name)
            end = self._case_ended_s.get(name)
            duration = (end - start) if (start is not None and end is not None) else None
            out[name] = {
                "status": self._status.get(name, "pending"),
                "phase": self._case_phase.get(name),
                "started_s": start,
                "ended_s": end,
                "duration_s": round(duration, 2) if duration is not None else None,
                "classification": self._classify(name),
                "fail_count": len(self._case_fails.get(name, [])),
                "fails": [dict(f) for f in self._case_fails.get(name, [])],
                "pending": self._case_pending.get(name),
            }
        return out

    def snapshot(self) -> dict:
        """상태 비교/직렬화용 평면 dict (monotonic 일관성 검증에 사용)."""
        return {
            "run_id": self.run_id,
            "plan": self.plan,
            "board": self.board,
            "total_cases": self.total_cases,
            "completed_cases": self.completed_cases,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "current_case": self.current_case,
            "avg_case_duration_s": self.avg_case_duration_s,
            "elapsed_s": self.elapsed_s,
            "last_heartbeat_seq": self.last_heartbeat_seq,
            "run_ended": self.run_ended,
            "cases": list(self._cases),
            "status": dict(self._status),
            "fail_summaries": dict(self._fail_summaries),
            "fail_classification": self.fail_classification,
            "case_details": self.case_details,
            "pending_summaries": self.pending_summaries,
        }
