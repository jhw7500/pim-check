#!/usr/bin/env python3
"""이전 실행에서 실패한 13개 케이스를 schema 수정 후 재실행.

schema.yaml에 enable=true 강제 추가한 후, 이전에 FAIL났던 케이스들이
이제 PASS하는지 확인한다.
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
RESULT = BASE / "channel_retry_results.json"

TARGET_HOST = "192.168.0.5"
CASE_TIMEOUT = 900
WAIT_BETWEEN = 180
CHECK_INTERVAL = 5

# 이전 실행에서 FAIL났던 13개 케이스
FAILED_CASES = [
    "gen_720p_ch2_vflip_on",
    "gen_720p_ch3_vflip_on",
    "gen_720p_ch2_ae_on",
    "gen_720p_ch2_ae_off",
    "gen_720p_ch3_ae_on",
    "gen_720p_ch3_ae_off",
    "gen_fhd_ch1_vflip_on",   # SSH 타임아웃
    "gen_fhd_ch2_vflip_on",
    "gen_fhd_ch3_vflip_on",
    "gen_fhd_ch2_ae_on",
    "gen_fhd_ch2_ae_off",
    "gen_fhd_ch3_ae_on",
    "gen_fhd_ch3_ae_off",
]


def wait_for_ssh(timeout: int = WAIT_BETWEEN) -> bool:
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
    t0 = time.monotonic()
    started = datetime.now().isoformat()

    if not wait_for_ssh():
        return {"case": case_name, "started": started, "rc": -2,
                "elapsed_sec": round(time.monotonic() - t0, 1),
                "passed": "NO_SSH", "stdout_tail": [], "stderr_tail": [],
                "report": None}

    try:
        result = subprocess.run(
            ["python3", str(BASE / "pim_check.py"),
             "--case", case_name, "--duration", "0", "--json"],
            capture_output=True, text=True, timeout=CASE_TIMEOUT, cwd=str(BASE),
        )
        elapsed = time.monotonic() - t0
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
            "rc": result.returncode, "elapsed_sec": round(elapsed, 1),
            "passed": "PASS" if result.returncode == 0 else "FAIL",
            "stdout_tail": result.stdout.strip().split("\n")[-20:] if result.stdout else [],
            "stderr_tail": result.stderr.strip().split("\n")[-5:] if result.stderr else [],
            "report": report_data,
        }
    except subprocess.TimeoutExpired:
        return {"case": case_name, "started": started, "rc": -1,
                "elapsed_sec": round(time.monotonic() - t0, 1),
                "passed": "TIMEOUT", "stdout_tail": [], "stderr_tail": [],
                "report": None}


def main():
    print(f"Retry failed cases — {datetime.now().isoformat()}")
    print(f"Total: {len(FAILED_CASES)}")
    print()

    results = []
    for i, case in enumerate(FAILED_CASES, 1):
        t_start = datetime.now().strftime("%H:%M:%S")
        print(f"[{i}/{len(FAILED_CASES)}] {t_start} Running {case}...", flush=True)
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
    other = len(results) - pass_count - fail_count
    print(f"PASS: {pass_count}/{len(FAILED_CASES)}, FAIL: {fail_count}, OTHER: {other}")
    print("=" * 60)
    print()
    if fail_count:
        print("실패 케이스:")
        for r in results:
            if r["passed"] != "PASS":
                print(f"  [{r['passed']}] {r['case']} ({r['elapsed_sec']}s)")
                if r["report"] and "checks" in r["report"]:
                    for chk in r["report"]["checks"]:
                        if not chk.get("passed"):
                            print(f"       FAIL: {chk['name']} - {chk['reason'][:100]}")

    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
