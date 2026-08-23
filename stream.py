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
from ssh import SshClient, SshConnectionError, SshTimeoutError
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

        # Setup — 실행 결정은 다른 4개 경로(cli/web/parallel/plan)처럼 run_setup 에
        # 위임한다 (#77). run_setup 이 inject-only/ord-only 모드와 "이미 일치" skip 을
        # 정식 지원하므로, edgeconf 변경 유무는 phase 메시지 분기에만 쓴다 —
        # 여기서 케이스를 거르면 inject-only fault 케이스가 주입 없이 PASS 한다.
        setup_config = profile.get("setup")
        setup_mgr = SetupManager(ssh)
        setup_changed = False
        if setup_config:
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
            try:
                setup_changed = setup_mgr.run_setup(setup_config)
                if setup_changed:
                    self._emit("phase", {"phase": "setup", "message": "Setup complete", "ok": True})
            except (TimeoutError, SshTimeoutError, SshConnectionError) as e:
                # Ssh* 예외는 TimeoutError 계열이 아니다 — 안 잡으면 러너 스레드가
                # 죽어 부분 적용/주입된 fault 가 보드에 남고, done 없이 스트림이
                # 매달린다. 복구를 시도하고 반드시 닫는다. 스냅샷은 변경 전에
                # 찍히므로 이 경로의 복원은 케이스 전 상태로 간다.
                self._emit("error", {"message": f"Setup failed: {e}"})
                self._emit("phase", {"phase": "teardown", "message": "Recovering after setup failure..."})
                try:
                    setup_mgr.run_teardown(setup_config, profile.get("teardown") or {})
                    self._emit("phase", {"phase": "teardown", "message": "Teardown complete", "ok": True})
                except Exception:
                    # teardown 은 best-effort — 어떤 예외도 done 전달을 막으면 안 된다
                    # (막으면 이 블록이 고치는 매달림을 복구 경로에서 재현한다).
                    self._emit("warning", {"message": "Teardown failed"})
                self._emit("done", {"status": "ERROR"})
                return

        # 체크 실행
        self._emit("phase", {"phase": "checks", "message": "Running checks..."})
        engine = Engine(ssh, profile)
        config = profile.get("checks", {})
        results = []

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

        # 결과 집계
        total = len(results)
        passed_count = sum(1 for r in results if r["passed"])
        real_fails = [r for r in results if not r["passed"] and "known_issue" not in r]
        if not real_fails:
            status = "PASS" if not any("known_issue" in r for r in results) else "WARN"
        else:
            status = "FAIL"

        # 히스토리 저장
        if self.reports_dir:
            append_result(results, self.case_name, self.host, 1, 1, self.reports_dir)
            save_dashboard(self.reports_dir)

        # Teardown — 복원은 setup 이 바꿨을 때만, 복구는 정의되면 항상 (pim-check#75 리뷰)
        teardown_config = profile.get("teardown") or {}
        if (setup_config and setup_changed) or teardown_config.get("recovery_command"):
            self._emit("phase", {"phase": "teardown", "message": "Restoring config..."})
            try:
                setup_mgr.run_teardown(
                    setup_config if setup_changed else {}, teardown_config)
                self._emit("phase", {"phase": "teardown", "message": "Teardown complete", "ok": True})
            except Exception:
                # best-effort — SSH 예외 등이 새면 done 없이 스레드가 죽어
                # 검사 결과가 스트림에 도달하지 못한다.
                self._emit("warning", {"message": "Teardown failed"})

        self._emit("done", {
            "status": status,
            "passed": passed_count,
            "total": total,
            "message": f"{status}: {passed_count}/{total} checks passed",
        })


def format_sse(event_type: str, data: dict) -> str:
    """SSE 형식 문자열을 생성한다."""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"
