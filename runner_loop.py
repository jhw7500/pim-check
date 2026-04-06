"""
runner_loop.py — Docker 컨테이너용 정기 실행 루프

환경변수로 설정을 받아 지정 간격으로 테스트를 반복 실행한다.
결과는 reports/ 볼륨에 저장되어 dashboard 서비스와 공유된다.
"""
from __future__ import annotations

import os
import sys
import time

from pim_check import run_case, list_cases
from history import save_dashboard

PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def main():
    host = os.environ.get("TARGET_HOST", "192.168.0.5")
    user = os.environ.get("TARGET_USER", "root")
    password = os.environ.get("TARGET_PASSWORD", "root")
    interval = int(os.environ.get("RUN_INTERVAL", "300"))
    case = os.environ.get("RUN_CASE", "") or None
    tag = os.environ.get("RUN_TAG", "") or None

    print(f"pim-check runner: host={host}, interval={interval}s, case={case}, tag={tag}")
    print(f"Reports: {REPORTS_DIR}")

    while True:
        try:
            if case:
                cases = [case]
            else:
                cases = list_cases(include_generated=True, tag=tag)

            if not cases:
                print("No cases to run.")
            else:
                print(f"\nRunning {len(cases)} case(s)...")
                for c in cases:
                    print(f"  [{c}]", end=" ", flush=True)
                    ret = run_case(c, host, user, password, None,
                                   save_json=False, save_html=False,
                                   save_history=True, quiet=True)
                    print("PASS" if ret == 0 else "FAIL")

                save_dashboard(REPORTS_DIR)
                print(f"Dashboard updated. Next run in {interval}s")

        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()
