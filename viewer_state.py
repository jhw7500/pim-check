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
        # 절대 시각 (ISO 8601 문자열) — run_start/run_end 이벤트의 ts 캡처.
        # 상대 시간(elapsed_s/eta) 외에 측정 시작/완료 시각을 UI 에 표시하기 위함.
        # 이벤트에 ts 가 없으면 None 유지.
        self.start_ts: str | None = None
        self.end_ts: str | None = None
        self._cases: list[str] = []           # plan 순서 유지
        self._status: dict[str, str] = {}      # name -> pending/running/pass/fail
        self._fail_summaries: dict[str, str] = {}  # name -> reason (발생 순서)
        # 케이스별 상세(드릴다운 + 일시/최종 fail 구분)용 추가 상태.
        self._case_fails: dict[str, list[dict]] = {}   # name -> [{check,reason,ts,elapsed_s}]
        self._case_phase: dict[str, str] = {}          # name -> 마지막 phase
        self._case_started_s: dict[str, float] = {}    # name -> 첫 case_start elapsed_s
        self._case_ended_s: dict[str, float] = {}      # name -> 마지막 case_end elapsed_s
        # 케이스별 절대 시각 (ISO 8601). 케이스 detail UI 에서 측정/완료 시각을
        # 절대값으로 표시. 상대(*_s) 와 병렬로 보존 — 같은 *_s 정책 (started 는
        # setdefault, ended 는 마지막 값 덮어쓰기).
        self._case_started_ts: dict[str, str] = {}
        self._case_ended_ts: dict[str, str] = {}
        self._case_pending: dict[str, str] = {}        # name -> 마지막 '준비 중' reason (fault 아님)
        self._case_desc: dict[str, str] = {}           # name -> 케이스 설명 (case_start)
        self._case_checklist: dict[str, list[dict]] = {}  # name -> [{name,command,expected}]
        # name -> {item_name: {actual, passed}} (case_end 의 항목별 실측값)
        self._case_checklist_results: dict[str, dict] = {}
        # name -> check_name -> (is_pending, reason) (fail/pending 이벤트, 스냅샷마다 덮어씀)
        # is_pending=True: settling/준비 중, False: 실제 fault.
        # check_pass 이벤트 수신 시 해당 check 항목 제거.
        self._case_latest_reason_by_check: dict[str, dict[str, tuple[bool, str]]] = {}
        # name -> set of check names that emitted any event (pass/fail/pending)
        # 비어있으면 아직 첫 스냅샷 미완.
        self._case_checks_seen: dict[str, set[str]] = {}

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
            # 절대 시각 캡처 — run_start 만이 측정 시작 시각의 권위 값. case_start
            # 의 ts 는 첫 case 가 시작된 시각이라 plan 로드/접속 오버헤드를 놓친다.
            start_ts = event.get("ts")
            if isinstance(start_ts, str):
                self.start_ts = start_ts
            # 대기 케이스도 검증 항목을 미리 보여주도록 run_start 의 plan 을 캡처.
            for cname, plan in (event.get("case_plans") or {}).items():
                if not isinstance(plan, dict):
                    continue
                if plan.get("desc") is not None:
                    self._case_desc[cname] = plan.get("desc")
                if plan.get("checklist") is not None:
                    self._case_checklist[cname] = list(plan.get("checklist"))
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
                start_ts = event.get("ts")
                if isinstance(start_ts, str):
                    self._case_started_ts.setdefault(name, start_ts)
                desc = event.get("case_desc")
                if desc is not None:
                    self._case_desc[name] = desc
                checklist = event.get("checklist")
                if checklist is not None:
                    self._case_checklist[name] = list(checklist)
                # 새 케이스 시작 → 이전 스냅샷 체크 상태 초기화
                self._case_latest_reason_by_check.pop(name, None)
                self._case_checks_seen.pop(name, None)
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
                cr = event.get("checklist_results")
                if isinstance(cr, list):
                    self._case_checklist_results[name] = {
                        it.get("name"): it for it in cr
                        if isinstance(it, dict) and it.get("name") is not None
                    }
                phase = event.get("phase")
                if phase is not None:
                    self._case_phase[name] = phase
                if isinstance(es, (int, float)):
                    self._case_ended_s[name] = float(es)
                end_ts = event.get("ts")
                if isinstance(end_ts, str):
                    self._case_ended_ts[name] = end_ts
                # 케이스 종료 → 준비 중/체크 상태 정리.
                self._case_pending.pop(name, None)
                self._case_latest_reason_by_check.pop(name, None)
                self._case_checks_seen.pop(name, None)
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
            end_ts = event.get("ts")
            if isinstance(end_ts, str):
                self.end_ts = end_ts
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
                check = event.get("check")
                self._case_fails.setdefault(name, []).append({
                    "check": check,
                    "reason": reason,
                    "ts": event.get("ts"),
                    "elapsed_s": event.get("elapsed_s"),
                })
                # 체크별 최신 실패 reason 기록 (스냅샷 경계마다 덮어씀 → 최신 상태 반영).
                if check:
                    self._case_latest_reason_by_check.setdefault(name, {})[check] = (False, reason)
                    self._case_checks_seen.setdefault(name, set()).add(check)
                # 실제 fault 가 떴으면 더 이상 '준비 중' 아님 — pending 해제
                # (안 그러면 드릴다운이 ⚠ FAULT 와 ⏳ 준비 중 을 동시 표시).
                self._case_pending.pop(name, None)
        elif et == "pending":
            # 안정화 미달(준비 중) — fault 아님. 카운트/분류에 영향 주지 않고
            # 현재 케이스가 '준비 중'임을 표시하는 용도로만 마지막 reason 을 보존한다.
            name = event.get("case_name")
            if name is not None:
                reason = event.get("reason") or ""
                self._case_pending[name] = reason
                # 체크별 최신 pending reason 기록 (pending 은 fail 을 덮어씀 — 같은 체크의
                # 최신 스냅샷 결과가 settling 으로 바뀌었음을 의미).
                check = event.get("check")
                if check:
                    self._case_latest_reason_by_check.setdefault(name, {})[check] = (True, reason)
                    self._case_checks_seen.setdefault(name, set()).add(check)
        elif et == "check_pass":
            # 체크 통과 — 해당 체크의 latest reason 제거(더 이상 fail/pending 아님).
            name = event.get("case_name")
            check = event.get("check")
            if name is not None and check:
                self._case_latest_reason_by_check.setdefault(name, {}).pop(check, None)
                self._case_checks_seen.setdefault(name, set()).add(check)
                # pending 항목이 남아있지 않으면 케이스 준비 중 표시도 해제.
                if not any(
                    is_p
                    for is_p, _ in self._case_latest_reason_by_check.get(name, {}).values()
                ):
                    self._case_pending.pop(name, None)
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

    def _deduped_fails(self, name: str) -> list[dict]:
        """동일 (check, reason) fault 이벤트를 묶어 count 와 함께 반환.

        until_pass 모니터가 ~interval 마다 재샘플링하면 지속 결함이 같은 fault 를
        반복 emit 하는데(루프처럼 보임), 드릴다운에서 1줄(×count)로 접는다.
        elapsed_s 는 첫 발생, last_elapsed_s 는 마지막 발생.
        """
        out: list[dict] = []
        index: dict[tuple, int] = {}
        for f in self._case_fails.get(name, []):
            key = (f.get("check"), f.get("reason"))
            es = f.get("elapsed_s")
            if key in index:
                entry = out[index[key]]
                entry["count"] += 1
                if isinstance(es, (int, float)):
                    entry["last_elapsed_s"] = es
            else:
                index[key] = len(out)
                out.append({
                    "check": f.get("check"),
                    "reason": f.get("reason"),
                    "count": 1,
                    "elapsed_s": es,
                    "last_elapsed_s": es,
                })
        return out

    def _checklist_view(self, name: str) -> tuple[list[dict], int]:
        """케이스의 검증 항목 + 항목별 상태 유도, (checklist, passed_count) 반환.

        항목별 상태는 case 결과 + 실시간 check 이벤트로부터 유도한다:
          - case pass/fail 종료 후 → per-item 실측값 or reason 매칭
          - case running + 첫 스냅샷 완료 → check_pass/fail/pending 이벤트로 추론:
              pending reason 에 항목명 있음 → "pending" (settling)
              fail reason 에 항목명 있음    → "fail"
              어느 reason 에도 없음          → "pass" (이미 통과)
          - case running + 첫 스냅샷 미완 → "running" (아직 대기)
          - pending(미시작)                 → "pending"
        """
        items = self._case_checklist.get(name)
        if not items:
            return [], 0
        status = self._status.get(name, "pending")
        reason = self._fail_summaries.get(name, "")
        per_item = self._case_checklist_results.get(name, {})
        # running 케이스의 per-item 상태 추론용
        checks_seen = self._case_checks_seen.get(name)  # None 이면 첫 스냅샷 미완
        latest_reasons = self._case_latest_reason_by_check.get(name, {})
        out: list[dict] = []
        passed = 0
        for item in items:
            iname = item.get("name", "")
            res = per_item.get(iname)
            if status in ("pass", "fail") and res is not None:
                # case 종료 + 항목별 실측값 있음 → per-item passed 가 권위(reason 매칭보다 정확)
                st = "pass" if res.get("passed") else "fail"
            elif status == "pass":
                st = "pass"
            elif status == "fail":
                st = "fail" if (iname and iname in reason) else "pass"
            elif status == "running":
                if not checks_seen:
                    st = "running"  # 첫 스냅샷 아직 미완
                else:
                    st = "pass"     # 기본: 어느 reason 에도 없음 = 이미 통과
                    if iname:
                        for is_pending, r in latest_reasons.values():
                            if iname in r:
                                if is_pending:
                                    if st == "pass":
                                        st = "pending"  # settling 중
                                else:
                                    st = "fail"         # 실제 fault (fail > pending 우선)
                                    break
            else:
                st = "pending"   # 아직 시작 안 한 케이스의 항목 = 대기
            if st == "pass":
                passed += 1
            entry = {**item, "status": st}
            if res is not None and res.get("actual") is not None:
                entry["actual"] = res.get("actual")
            out.append(entry)
        return out, passed

    @property
    def case_details(self) -> dict[str, dict]:
        """모든 케이스의 드릴다운 상세(상태/phase/소요시간/fail 목록/준비중/체크리스트)."""
        out: dict[str, dict] = {}
        for name in self._cases:
            start = self._case_started_s.get(name)
            end = self._case_ended_s.get(name)
            duration = (end - start) if (start is not None and end is not None) else None
            checklist, checks_passed = self._checklist_view(name)
            out[name] = {
                "status": self._status.get(name, "pending"),
                "phase": self._case_phase.get(name),
                "started_s": start,
                "ended_s": end,
                "started_ts": self._case_started_ts.get(name),
                "ended_ts": self._case_ended_ts.get(name),
                "duration_s": round(duration, 2) if duration is not None else None,
                "classification": self._classify(name),
                "fail_count": len(self._case_fails.get(name, [])),
                "fails": self._deduped_fails(name),
                "pending": self._case_pending.get(name),
                "desc": self._case_desc.get(name),
                "checklist": checklist,
                "checks_total": len(checklist),
                "checks_passed": checks_passed,
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
