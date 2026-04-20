#!/usr/bin/env python3
"""채널별 vflip/ae 케이스 실제 실행 검증 스크립트.

32개 케이스를 순차 실행하여 edgeconf 변경 → 재부팅 → ISP 레지스터 검증.
- 각 케이스 타임아웃: 900초 (재부팅 + stabilize 충분)
- 케이스 사이 SSH connectivity 대기 (최대 120초)
- 모든 결과를 JSON 로그로 저장하고 요약 출력
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / "channel_verify.log"
RESULT = BASE / "channel_verify_results.json"

TARGET_HOST = "192.168.0.5"
CASE_TIMEOUT = 900      # 15분 per case
WAIT_BETWEEN = 120      # 최대 2분 SSH 복구 대기
CHECK_INTERVAL = 5

CASES = [
    # 720p vflip
    "gen_720p_ch0_vflip_off", "gen_720p_ch0_vflip_on",
    "gen_720p_ch1_vflip_off", "gen_720p_ch1_vflip_on",
    "gen_720p_ch2_vflip_off", "gen_720p_ch2_vflip_on",
    "gen_720p_ch3_vflip_off", "gen_720p_ch3_vflip_on",
    # 720p ae
    "gen_720p_ch0_ae_on", "gen_720p_ch0_ae_off",
    "gen_720p_ch1_ae_on", "gen_720p_ch1_ae_off",
    "gen_720p_ch2_ae_on", "gen_720p_ch2_ae_off",
    "gen_720p_ch3_ae_on", "gen_720p_ch3_ae_off",
    # fhd vflip
    "gen_fhd_ch0_vflip_off", "gen_fhd_ch0_vflip_on",
    "gen_fhd_ch1_vflip_off", "gen_fhd_ch1_vflip_on",
    "gen_fhd_ch2_vflip_off", "gen_fhd_ch2_vflip_on",
    "gen_fhd_ch3_vflip_off", "gen_fhd_ch3_vflip_on",
    # fhd ae
    "gen_fhd_ch0_ae_on", "gen_fhd_ch0_ae_off",
    "gen_fhd_ch1_ae_on", "gen_fhd_ch1_ae_off",
    "gen_fhd_ch2_ae_on", "gen_fhd_ch2_ae_off",
    "gen_fhd_ch3_ae_on", "gen_fhd_ch3_ae_off",
]


def wait_for_ssh(timeout: int = WAIT_BETWEEN) -> bool:
    """SSH 연결 복구 대기. 연결 성공 시 True 반환."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["sshpass", "-p", "root", "ssh",
             "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5",
             f"root@{TARGET_HOST}", "echo ok"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and "ok" in r.stdout:
            return True
        time.sleep(CHECK_INTERVAL)
    return False


def run_case(case_name: str) -> dict:
    """단일 케이스 실행 후 결과 dict 반환."""
    t0 = time.monotonic()
    started = datetime.now().isoformat()

    # SSH 연결 대기
    if not wait_for_ssh():
        return {
            "case": case_name, "started": started, "rc": -2,
            "elapsed_sec": round(time.monotonic() - t0, 1),
            "passed": "NO_SSH", "stdout_tail": [], "stderr_tail": [],
            "report": None,
        }

    try:
        result = subprocess.run(
            ["python3", str(BASE / "pim_check.py"),
             "--case", case_name, "--duration", "0", "--json"],
            capture_output=True, text=True, timeout=CASE_TIMEOUT, cwd=str(BASE),
        )
        elapsed = time.monotonic() - t0
        stdout = result.stdout
        stderr = result.stderr
        rc = result.returncode
        reports = sorted((BASE / "reports").glob(f"{case_name}_*.json"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        report_data = None
        if reports:
            try:
                report_data = json.loads(reports[0].read_text())
            except Exception:
                pass
        return {
            "case": case_name, "started": started,
            "rc": rc, "elapsed_sec": round(elapsed, 1),
            "passed": "PASS" if rc == 0 else "FAIL",
            "stdout_tail": stdout.strip().split("\n")[-20:] if stdout else [],
            "stderr_tail": stderr.strip().split("\n")[-5:] if stderr else [],
            "report": report_data,
        }
    except subprocess.TimeoutExpired:
        return {"case": case_name, "started": started, "rc": -1,
                "elapsed_sec": round(time.monotonic() - t0, 1),
                "passed": "TIMEOUT", "stdout_tail": [], "stderr_tail": [],
                "report": None}


def main():
    print(f"Channel verify run — {datetime.now().isoformat()}")
    print(f"Total cases: {len(CASES)}")
    print(f"Per-case timeout: {CASE_TIMEOUT}s")
    print(f"Results: {RESULT}")
    print()

    results = []
    for i, case in enumerate(CASES, 1):
        t_start = datetime.now().strftime("%H:%M:%S")
        print(f"[{i}/{len(CASES)}] {t_start} Running {case}...", flush=True)
        r = run_case(case)
        results.append(r)
        checks_info = ""
        if r.get("report") and "checks" in r["report"]:
            pc = sum(1 for c in r["report"]["checks"] if c.get("passed"))
            tc = len(r["report"]["checks"])
            checks_info = f" [{pc}/{tc} checks]"
        print(f"   → {r['passed']} ({r['elapsed_sec']}s){checks_info}", flush=True)
        RESULT.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    print()
    print("=" * 60)
    pass_count = sum(1 for r in results if r["passed"] == "PASS")
    fail_count = sum(1 for r in results if r["passed"] == "FAIL")
    timeout_count = sum(1 for r in results if r["passed"] == "TIMEOUT")
    no_ssh_count = sum(1 for r in results if r["passed"] == "NO_SSH")
    print(f"PASS: {pass_count}/{len(CASES)}, FAIL: {fail_count}, "
          f"TIMEOUT: {timeout_count}, NO_SSH: {no_ssh_count}")
    print("=" * 60)
    print()
    if fail_count or timeout_count or no_ssh_count:
        print("실패 케이스:")
        for r in results:
            if r["passed"] != "PASS":
                print(f"  [{r['passed']}] {r['case']} ({r['elapsed_sec']}s)")
                if r["report"] and "checks" in r["report"]:
                    for chk in r["report"]["checks"]:
                        if not chk.get("passed"):
                            print(f"       FAIL: {chk['name']} - {chk['reason'][:100]}")

    sys.exit(0 if fail_count == 0 and timeout_count == 0 and no_ssh_count == 0 else 1)


if __name__ == "__main__":
    main()
