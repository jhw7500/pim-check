#!/usr/bin/env python3
"""pim-check QA Agent — E2E 시나리오 기반 사용자 관점 기능 검증.

Usage:
    python qa_agent.py                    # 전체 실행 (타겟 필요)
    python qa_agent.py --no-target        # 타겟 없이 CLI/WEB만 검증
    python qa_agent.py --scenario cli     # 특정 시나리오만
    python qa_agent.py --json             # JSON 리포트 출력
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable
PIM_CHECK = str(BASE_DIR / "pim_check.py")
WEB_PY = str(BASE_DIR / "web.py")
PROFILES_DIR = str(BASE_DIR / "profiles")


class QAResult:
    """단일 QA 체크 결과."""
    def __init__(self, scenario: str, name: str, passed: bool,
                 detail: str = "", duration_ms: int = 0):
        self.scenario = scenario
        self.name = name
        self.passed = passed
        self.detail = detail
        self.duration_ms = duration_ms

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """서브프로세스 실행 후 (returncode, stdout, stderr) 반환."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=str(BASE_DIR))
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


def _timed(fn):
    """실행 시간(ms) 측정 데코레이터."""
    start = time.monotonic()
    result = fn()
    elapsed = int((time.monotonic() - start) * 1000)
    return result, elapsed


# ── Scenario: CLI ──────────────────────────────────────────────

def scenario_cli() -> list[QAResult]:
    results = []

    # --help
    def check_help():
        rc, out, err = _run([PYTHON, PIM_CHECK, "--help"])
        return rc == 0 and "usage" in out.lower()
    passed, ms = _timed(check_help)
    results.append(QAResult("cli", "--help 출력", passed, duration_ms=ms))

    # --list
    def check_list():
        rc, out, err = _run([PYTHON, PIM_CHECK, "--list"])
        lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
        return rc == 0 and len(lines) > 5, f"{len(lines)} cases"
    (passed, detail), ms = _timed(check_list)
    results.append(QAResult("cli", "--list 케이스 목록", passed, detail, ms))

    # --dry-run
    def check_dryrun():
        rc, out, err = _run([PYTHON, PIM_CHECK, "--case", "board_hw_check", "--dry-run"])
        return rc == 0 and "no setup" in out.lower()
    passed, ms = _timed(check_dryrun)
    results.append(QAResult("cli", "--dry-run 동작", passed, duration_ms=ms))

    # --list with generated cases
    def check_generated():
        rc, out, err = _run([PYTHON, PIM_CHECK, "--list"])
        has_generated = any("gen_" in line or "generated" in line.lower()
                           for line in out.splitlines())
        case_count = len([ln for ln in out.strip().splitlines() if ln.strip()])
        return rc == 0, f"generated={'Y' if has_generated else 'N'}, total={case_count}"
    (passed, detail), ms = _timed(check_generated)
    results.append(QAResult("cli", "--list generated 케이스 포함", passed, detail, ms))

    return results


# ── Scenario: Target ───────────────────────────────────────────

def scenario_target(host: str, user: str, password: str) -> list[QAResult]:
    results = []

    # SSH 연결
    def check_ssh():
        rc, out, err = _run(["sshpass", "-p", password, "ssh",
                             "-o", "StrictHostKeyChecking=no",
                             "-o", "ConnectTimeout=5",
                             f"{user}@{host}", "echo ok"], timeout=10)
        return rc == 0 and "ok" in out
    passed, ms = _timed(check_ssh)
    results.append(QAResult("target", f"SSH 연결 ({host})", passed, duration_ms=ms))

    if not passed:
        results.append(QAResult("target", "타겟 체크 스킵", False, "SSH 연결 실패"))
        return results

    # snapshot 실행 (board_hw_check, duration=0)
    def check_snapshot():
        rc, out, err = _run([PYTHON, PIM_CHECK, "--case", "board_hw_check",
                             "--duration", "0", "--host", host], timeout=180)
        has_report = "pim-check Report" in out
        detail = out.strip().split("\n")[-1] if out.strip() else err.strip()[:100]
        return has_report, detail
    (passed, detail), ms = _timed(check_snapshot)
    results.append(QAResult("target", "snapshot 실행 (board_hw_check)", passed, detail, ms))

    # JSON/HTML/JUnit 리포트 — reports/ 디렉토리에 생성됨
    reports_dir = BASE_DIR / "reports"
    before_files = set(reports_dir.glob("*")) if reports_dir.exists() else set()

    def check_json():
        rc, out, err = _run([PYTHON, PIM_CHECK, "--case", "config_integrity",
                             "--duration", "0", "--host", host, "--json"], timeout=180)
        new_files = set(reports_dir.glob("config_integrity_*.json")) - before_files
        if new_files:
            f = next(iter(new_files))
            data = json.loads(f.read_text())
            return "checks" in data or "results" in data, f"file={f.name}"
        return False, "JSON 파일 미생성"
    (passed, detail), ms = _timed(check_json)
    results.append(QAResult("target", "JSON 리포트 생성", passed, detail, ms))

    mid_files = set(reports_dir.glob("*")) if reports_dir.exists() else set()

    def check_html():
        rc, out, err = _run([PYTHON, PIM_CHECK, "--case", "config_integrity",
                             "--duration", "0", "--host", host, "--html"], timeout=180)
        new_files = set(reports_dir.glob("config_integrity_*.html")) - mid_files
        if new_files:
            f = next(iter(new_files))
            content = f.read_text()
            return "<html" in content.lower(), f"file={f.name}"
        return False, "HTML 파일 미생성"
    (passed, detail), ms = _timed(check_html)
    results.append(QAResult("target", "HTML 리포트 생성", passed, detail, ms))

    mid_files2 = set(reports_dir.glob("*")) if reports_dir.exists() else set()

    def check_junit():
        rc, out, err = _run([PYTHON, PIM_CHECK, "--case", "config_integrity",
                             "--duration", "0", "--host", host, "--junit"], timeout=180)
        new_files = set(reports_dir.glob("config_integrity_*.xml")) - mid_files2
        if new_files:
            f = next(iter(new_files))
            content = f.read_text()
            return "<testsuite" in content, f"file={f.name}"
        return False, "JUnit XML 파일 미생성"
    (passed, detail), ms = _timed(check_junit)
    results.append(QAResult("target", "JUnit XML 리포트 생성", passed, detail, ms))

    return results


# ── Scenario: Web ──────────────────────────────────────────────

def _wait_for_server(port: int, timeout: int = 10) -> bool:
    """서버가 응답할 때까지 대기."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def scenario_web(host: str) -> list[QAResult]:
    results = []
    port = 18900

    # 웹 서버 시작
    proc = subprocess.Popen(
        [PYTHON, WEB_PY, "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(BASE_DIR),
    )

    try:
        if not _wait_for_server(port):
            results.append(QAResult("web", "웹 서버 시작", False, "서버 응답 없음"))
            return results
        results.append(QAResult("web", "웹 서버 시작", True))

        # GET / (대시보드)
        def check_dashboard():
            req = urllib.request.urlopen(f"http://localhost:{port}/", timeout=10)
            body = req.read()
            return req.status == 200 and len(body) > 1000, f"size={len(body)}"
        (passed, detail), ms = _timed(check_dashboard)
        results.append(QAResult("web", "GET / 대시보드", passed, detail, ms))

        # GET /api/status
        def check_status():
            req = urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=10)
            data = json.loads(req.read())
            return "auto" in data and "active" in data, json.dumps(data)
        (passed, detail), ms = _timed(check_status)
        results.append(QAResult("web", "GET /api/status", passed, detail, ms))

        # GET /api/run?duration=0
        def check_api_run():
            url = f"http://localhost:{port}/api/run?case=config_integrity&duration=0"
            req = urllib.request.urlopen(url, timeout=120)
            data = json.loads(req.read())
            has_fields = "status" in data and "checks" in data
            return has_fields, f"status={data.get('status')}, checks={len(data.get('checks', []))}"
        (passed, detail), ms = _timed(check_api_run)
        results.append(QAResult("web", "GET /api/run?duration=0", passed, detail, ms))

        # GET /api/run-selected?duration=0
        def check_api_run_selected():
            url = (f"http://localhost:{port}/api/run-selected"
                   f"?cases=config_integrity&duration=0")
            req = urllib.request.urlopen(url, timeout=120)
            data = json.loads(req.read())
            return "results" in data, f"count={data.get('count')}"
        (passed, detail), ms = _timed(check_api_run_selected)
        results.append(QAResult("web", "GET /api/run-selected?duration=0", passed, detail, ms))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)

    return results


# ── Report ─────────────────────────────────────────────────────

def print_report(all_results: list[QAResult], as_json: bool = False):
    """결과를 터미널 또는 JSON으로 출력."""
    if as_json:
        print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "total": len(all_results),
            "passed": sum(1 for r in all_results if r.passed),
            "failed": sum(1 for r in all_results if not r.passed),
            "results": [r.to_dict() for r in all_results],
        }, ensure_ascii=False, indent=2))
        return

    print(f"\n{'=' * 60}")
    print(f"  pim-check QA Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}\n")

    current_scenario = None
    for r in all_results:
        if r.scenario != current_scenario:
            current_scenario = r.scenario
            print(f"  [{current_scenario.upper()}]")

        mark = "[+]" if r.passed else "[X]"
        detail = f" — {r.detail}" if r.detail else ""
        time_str = f" ({r.duration_ms}ms)" if r.duration_ms else ""
        print(f"    {mark} {r.name}{detail}{time_str}")

    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    failed = total - passed
    status = "PASS" if failed == 0 else "FAIL"

    print(f"\n{'=' * 60}")
    print(f"  Result: {status} ({passed}/{total} passed, {failed} failed)")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="pim-check QA Agent")
    parser.add_argument("--no-target", action="store_true",
                        help="타겟 없이 CLI/WEB 구조만 검증")
    parser.add_argument("--scenario", choices=["cli", "target", "web", "all"],
                        default="all", help="실행할 시나리오")
    parser.add_argument("--host", default="192.168.0.5", help="타겟 호스트")
    parser.add_argument("--user", default="root", help="타겟 사용자")
    parser.add_argument("--password", default="root", help="타겟 비밀번호")
    parser.add_argument("--json", dest="json_output", action="store_true",
                        help="JSON 형식으로 출력")
    args = parser.parse_args()

    all_results: list[QAResult] = []
    scenarios = [args.scenario] if args.scenario != "all" else ["cli", "target", "web"]

    if args.no_target:
        scenarios = [s for s in scenarios if s != "target"]

    for scenario in scenarios:
        if scenario == "cli":
            all_results.extend(scenario_cli())
        elif scenario == "target":
            all_results.extend(scenario_target(args.host, args.user, args.password))
        elif scenario == "web":
            all_results.extend(scenario_web(args.host))

    print_report(all_results, as_json=args.json_output)

    failed = sum(1 for r in all_results if not r.passed)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
