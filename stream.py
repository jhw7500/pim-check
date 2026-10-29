"""
stream.py — 실시간 테스트 실행 + SSE 스트리밍

Server-Sent Events로 체크별 결과를 실시간 전송한다.
외부 의존성 없음 (표준 라이브러리만 사용).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime

from config import load_profile
from engine import Engine
from setup import ORD_VCM_PATH, SetupManager
from ssh import SshClient
from history import append_result, save_dashboard

PROFILES_DIR = ""  # web.py에서 설정


class StreamRunner:
    """테스트를 실행하면서 진행 상황을 큐에 넣는 클래스."""

    def __init__(self, case_name: str | None, host: str,
                 user: str = "root", password: str = "root",
                 profiles_dir: str = "", reports_dir: str = ""):
        self.case_name = case_name
        self.host = host
        self.user = user
        self.password = password
        self.profiles_dir = profiles_dir
        self.reports_dir = reports_dir
        self.events: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    def _emit(self, event_type: str, data: dict):
        """이벤트를 큐에 추가한다."""
        data["timestamp"] = datetime.now().isoformat()
        self.events.put({"event": event_type, "data": data})

    def start(self):
        """백그라운드 스레드에서 테스트를 시작한다."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """테스트 실행 메인 로직."""
        self._emit("start", {
            "case": self.case_name,
            "host": self.host,
            "message": f"Starting {self.case_name or 'healthcheck'} on {self.host}",
        })

        # Failures before any setup mutation must not leave an SSE reader
        # waiting forever for the terminal "done" event.
        try:
            profile = load_profile(self.profiles_dir, case=self.case_name)
            profile["target"]["host"] = self.host
            profile["target"]["user"] = self.user
            profile["target"]["password"] = self.password

            ssh = SshClient(self.host, self.user, self.password)

            # 연결 확인
            self._emit("phase", {"phase": "connect", "message": f"Connecting to {self.host}..."})
            if not ssh.check_connectivity():
                self._emit("error", {"message": f"Cannot connect to {self.host}"})
                self._emit("done", {"status": "ERROR"})
                return

            self._emit("phase", {"phase": "connect", "message": "Connected", "ok": True})

            # Preflight
            missing = ssh.preflight_check()
            if missing:
                self._emit("warning", {"message": f"Missing tools: {', '.join(missing)}"})
        except Exception as exc:
            self._emit("error", {"message": f"Preflight failed: {exc}"})
            self._emit("done", {"status": "ERROR"})
            return

        # Setup — 실행 결정은 다른 4개 경로(cli/web/parallel/plan)처럼 run_setup 에
        # 위임한다 (#77). run_setup 이 inject-only/ord-only 모드와 "이미 일치" skip 을
        # 정식 지원하므로, edgeconf 변경 유무는 phase 메시지 분기에만 쓴다 —
        # 여기서 케이스를 거르면 inject-only fault 케이스가 주입 없이 PASS 한다.
        try:
            setup_config = profile.get("setup")
            setup_mgr = SetupManager(ssh)
        except Exception as exc:
            self._emit("error", {"message": f"Setup initialization failed: {exc}"})
            self._emit("done", {"status": "ERROR"})
            return
        setup_changed = False
        if setup_config:
            try:
                changes = setup_config.get("edgeconf_changes", {})
                ord_changes = setup_config.get("ord_vcm_changes", {})
                if changes or ord_changes:
                    self._emit("phase", {"phase": "setup", "message": "Checking config..."})
                    # skip 예고는 run_setup 과 같은 기준(edge+ord 모두 일치)으로 —
                    # edge 만 보면 ord 가 다른 결합 프로파일에서 "skip" 직후 재부팅한다.
                    edge_match = (not changes) or setup_mgr.check_current(changes)
                    ord_match = (not ord_changes) or setup_mgr.check_current(ord_changes, ORD_VCM_PATH)
                    if edge_match and ord_match:
                        self._emit("phase", {"phase": "setup", "message": "Config matches, skip reboot", "ok": True})
                    else:
                        total = len(changes) + len(ord_changes)
                        self._emit("phase", {"phase": "setup", "message": f"Applying {total} changes + reboot..."})
                else:
                    self._emit("phase", {"phase": "setup", "message": "No config changes — applying setup (inject)..."})
                setup_changed = setup_mgr.run_setup(setup_config)
                if setup_changed:
                    self._emit("phase", {"phase": "setup", "message": "Setup complete", "ok": True})
            except Exception as e:
                # 설정 적용 검증의 RuntimeError도 부분 적용 후에 발생할 수 있다.
                self._emit("error", {"message": f"Setup failed: {e}"})
                if getattr(setup_mgr, "_recovery_outcome_uncertain", False) is True:
                    self._emit("error", {
                        "message": "FAULT_RECOVERY_UNVERIFIED: camera recovery outcome unknown; cleanup deferred",
                    })
                    self._emit("done", {"status": "ERROR", "recovery_unverified": True})
                    return
                # 변경 시도 전 거부에는 복원할 이번 실행의 변경이 없다.
                if getattr(setup_mgr, "_setup_mutation_attempted", None) is False:
                    self._emit("done", {"status": "ERROR"})
                    return
                self._emit("phase", {"phase": "teardown", "message": "Recovering after setup failure..."})
                try:
                    setup_mgr.run_teardown(setup_config, profile.get("teardown") or {})
                    self._emit("phase", {"phase": "teardown", "message": "Teardown complete", "ok": True})
                except Exception as cleanup_exc:
                    # A failed cleanup is not merely a warning: the target
                    # cannot be considered safe for another test.
                    reason = f"FAULT_RECOVERY_UNVERIFIED: {cleanup_exc}"
                    self._emit("warning", {"message": "Teardown failed"})
                    self._emit("error", {"message": reason})
                    self._emit("done", {"status": "ERROR",
                                        "recovery_unverified": True,
                                        "message": reason})
                    return
                self._emit("done", {"status": "ERROR"})
                return

        # 체크 실행
        self._emit("phase", {"phase": "checks", "message": "Running checks..."})
        results = []
        execution_failed = False
        try:
            engine = Engine(ssh, profile)
            config = profile.get("checks", {})

            for check in engine.checks:
                self._emit("check_start", {"check": check.name})
                start_time = time.time()
                try:
                    data = check.collect(ssh, config)
                    passed, reason = check.validate(data, config)
                except Exception as exc:
                    data = {}
                    passed = False
                    reason = f"SSH_ERROR: {exc}"

                duration_ms = int((time.time() - start_time) * 1000)
                result = {
                    "name": check.name,
                    "passed": passed,
                    "reason": reason,
                    "data": data,
                    "duration_ms": duration_ms,
                }
                results.append(result)

                # known_issues 적용
                known_issues = profile.get("known_issues")
                if known_issues and not passed:
                    for ki in known_issues:
                        if check.name == ki["check"] and ki["reason_contains"] in reason:
                            result["known_issue"] = ki["label"]

                self._emit("check_result", {
                    "check": check.name,
                    "passed": passed,
                    "reason": reason,
                    "known_issue": result.get("known_issue", ""),
                    "duration_ms": duration_ms,
                })

        except Exception as exc:
            execution_failed = True
            reason = f"EXECUTION_EXCEPTION: {exc}"
            results.append({"name": "execution", "passed": False,
                            "reason": reason, "data": {}, "duration_ms": 0})
            self._emit("error", {"message": reason})

        # 결과 집계
        total = len(results)
        passed_count = sum(1 for r in results if r["passed"])
        real_fails = [r for r in results if not r["passed"] and "known_issue" not in r]
        if execution_failed:
            status = "ERROR"
        elif not real_fails:
            status = "PASS" if not any("known_issue" in r for r in results) else "WARN"
        else:
            status = "FAIL"

        # 히스토리는 teardown 완료 후 저장한다. 복구 실패를 PASS로 기록하지 않는다.
        # Teardown — 복원은 setup 이 바꿨을 때만, 복구는 정의되면 항상 (pim-check#75 리뷰)
        teardown_config = profile.get("teardown") or {}
        recovery_unverified = False
        if (setup_config and setup_changed) or teardown_config.get("recovery_command"):
            self._emit("phase", {"phase": "teardown", "message": "Restoring config..."})
            try:
                setup_mgr.run_teardown(
                    setup_config if setup_changed else {}, teardown_config)
                self._emit("phase", {"phase": "teardown", "message": "Teardown complete", "ok": True})
            except Exception as exc:
                # 복구 실패 상태에서 PASS를 내보내면 다음 시험이 오염된 보드를
                # 정상으로 오인한다. 스트림은 닫되 결과와 히스토리는 ERROR로 확정.
                recovery_unverified = True
                reason = f"FAULT_RECOVERY_UNVERIFIED: {exc}"
                self._emit("warning", {"message": "Teardown failed"})
                self._emit("error", {"message": reason})
                results.append({"name": "teardown_recovery", "passed": False,
                                "reason": reason, "data": {}, "duration_ms": 0})
                total += 1
                status = "ERROR"

        report_failure = None
        if self.reports_dir:
            try:
                append_result(results, self.case_name, self.host, 1, 1, self.reports_dir)
                save_dashboard(self.reports_dir)
            except Exception as exc:
                # Persistence is not part of the hardware recovery proof.
                # Always close the SSE stream; never hide a teardown failure.
                report_failure = f"Report persistence failed: {exc}"
                self._emit("error", {"message": report_failure})
                status = "ERROR"

        done = {
            "status": status,
            "passed": passed_count,
            "total": total,
            "message": f"{status}: {passed_count}/{total} checks passed",
        }
        if recovery_unverified:
            done["recovery_unverified"] = True
        if report_failure:
            done["message"] += f"; {report_failure}"
        self._emit("done", done)


def format_sse(event_type: str, data: dict) -> str:
    """SSE 형식 문자열을 생성한다."""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"
