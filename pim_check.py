#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import os
import sys

from config import load_profile
from engine import Engine
from reporter import Reporter
from setup import SetupManager
from ssh import SshClient, SshConnectionError

PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="pim-check — iMX8MP 타겟 QA 자동화 툴"
    )
    parser.add_argument("--case", type=str, default=None, help="실행할 테스트 케이스 이름")
    parser.add_argument("--all", action="store_true", help="모든 케이스 실행")
    parser.add_argument("--host", type=str, default=None, help="타겟 IP 주소")
    parser.add_argument("--user", type=str, default=None, help="SSH 유저")
    parser.add_argument("--password", type=str, default=None, help="SSH 비밀번호")
    parser.add_argument("--duration", type=int, default=None, help="모니터 duration 오버라이드 (초)")
    parser.add_argument("--list", action="store_true", help="사용 가능한 케이스 목록 출력")
    parser.add_argument("--learn", action="store_true", help="베이스라인 학습 모드")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 파일로 저장")
    parser.add_argument("--html", action="store_true", help="결과를 HTML 파일로 저장")
    parser.add_argument("--history", action="store_true", help="결과를 히스토리에 추가")
    parser.add_argument("--dry-run", action="store_true", help="재부팅 없이 설정 차이만 확인")
    parser.add_argument("--history-report", action="store_true", help="히스토리 대시보드 HTML 생성")
    parser.add_argument("--generate", action="store_true", help="스키마 기반 테스트 케이스 자동 생성")
    parser.add_argument("--include-generated", action="store_true", help="자동 생성된 케이스도 실행에 포함")
    return parser.parse_args(argv)


def list_cases(include_generated: bool = False) -> list[str]:
    """profiles/cases/*.yaml 글로브로 케이스 목록을 반환한다."""
    pattern = os.path.join(PROFILES_DIR, "cases", "*.yaml")
    paths = glob.glob(pattern)
    if include_generated:
        gen_pattern = os.path.join(PROFILES_DIR, "generated", "*.yaml")
        paths.extend(glob.glob(gen_pattern))
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in paths)


def run_case(case_name, host, user, password, duration, save_json=False,
             save_html=False, save_history=False) -> int:
    """단일 케이스를 실행하고 종료 코드를 반환한다 (0=PASS, 1=FAIL)."""
    profile = load_profile(PROFILES_DIR, case=case_name)

    # CLI 오버라이드 (지정된 값만 덮어씀)
    if host is not None:
        profile["target"]["host"] = host
    if user is not None:
        profile["target"]["user"] = user
    if password is not None:
        profile["target"]["password"] = password
    if duration is not None:
        profile["monitor"]["duration_sec"] = duration

    host = profile["target"].get("host", "192.168.0.5")
    user = profile["target"].get("user", "root")
    password = profile["target"].get("password", "root")
    effective_duration = profile["monitor"].get("duration_sec", 0)

    ssh = SshClient(host, user, password)

    print(f"Connecting to {host}...")

    if not ssh.check_connectivity():
        print(f"ERROR: Cannot connect to {host}")
        return 1

    # Amendment 3: Preflight check
    missing = ssh.preflight_check()
    if missing:
        print(f"WARNING: Missing tools on target: {', '.join(missing)}")
        print("Some checks may produce incomplete results.")

    # Setup (Phase 4): apply edgeconf changes if defined
    setup_config = profile.get("setup")
    setup_mgr = SetupManager(ssh)
    setup_changed = False
    if setup_config:
        try:
            setup_changed = setup_mgr.run_setup(setup_config)
        except TimeoutError as e:
            print(f"ERROR: Setup failed - {e}")
            return 1

    try:
        engine = Engine(ssh, profile)

        if effective_duration <= 0:
            results = engine.run_snapshot()
            collected, total = 1, 1
        else:
            results, collected, total = engine.run_monitor()

        reporter = Reporter()
        known_issues = profile.get("known_issues")
        print(reporter.format(results, case_name, collected, total, known_issues=known_issues))

        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

        if save_json:
            filepath = reporter.save_json(results, case_name, host, collected, total, report_dir)
            print(f"JSON report saved: {filepath}")

        if save_html:
            from html_reporter import save_html as _save_html
            filepath = _save_html(results, case_name, host, collected, total, report_dir)
            print(f"HTML report saved: {filepath}")

        if save_history:
            from history import append_result
            append_result(results, case_name, host, collected, total, report_dir)

        # known_issue가 붙은 FAIL은 실패로 치지 않음
        real_fails = [r for r in results if not r["passed"] and "known_issue" not in r]
        return 0 if not real_fails else 1
    finally:
        # Teardown: setup이 실제로 변경한 경우에만 복원
        if setup_config and setup_changed:
            try:
                setup_mgr.run_teardown(setup_config)
            except TimeoutError as e:
                print(f"WARNING: Teardown failed - {e}")


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.list:
        cases = list_cases(include_generated=args.include_generated)
        if cases:
            print("Available cases:")
            for c in cases:
                print(f"  {c}")
        else:
            print("No cases found in profiles/cases/")
        return 0

    if args.history_report:
        from history import save_dashboard
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        filepath = save_dashboard(report_dir)
        print(f"Dashboard saved: {filepath}")
        return 0

    if args.generate:
        from generator import generate_cases
        print("Generating test cases from schema...")
        generated = generate_cases(PROFILES_DIR)
        print(f"\n{len(generated)} case(s) generated.")
        return 0

    if args.learn:
        from learner import learn_baseline
        host = args.host or "192.168.0.5"
        ssh = SshClient(host, args.user or "root", args.password or "root")
        print(f"Learning baseline from {host}...")
        if not ssh.check_connectivity():
            print(f"ERROR: Cannot reach {host}")
            return 1
        yaml_output = learn_baseline(ssh, name=args.case)
        print(yaml_output)
        return 0

    if args.dry_run:
        from setup import SetupManager
        cases = [args.case] if args.case else list_cases(include_generated=args.include_generated)
        if not cases:
            print("No cases to check.")
            return 0
        host = args.host or "192.168.0.5"
        ssh = SshClient(host, args.user or "root", args.password or "root")
        if not ssh.check_connectivity():
            print(f"ERROR: Cannot connect to {host}")
            return 1
        setup_mgr = SetupManager(ssh)
        for case_name in cases:
            profile = load_profile(PROFILES_DIR, case=case_name)
            setup_config = profile.get("setup")
            if not setup_config or not setup_config.get("edgeconf_changes"):
                print(f"  {case_name}: no setup (snapshot only)")
                continue
            changes = setup_config["edgeconf_changes"]
            if setup_mgr.check_current(changes):
                print(f"  {case_name}: config matches (no reboot needed)")
            else:
                print(f"  {case_name}: config DIFFERS — would change:")
                for k, v in changes.items():
                    current = ssh.run(f"jq '{k}' /root/shared_v/edgeconf_pim.json")
                    if current is not None and current.strip('"') == str(v):
                        continue
                    print(f"    {k}: {current} → {v}")
        return 0

    if args.all:
        cases = list_cases(include_generated=args.include_generated)
        if not cases:
            print("No cases found.")
            return 1
        worst = 0
        for case_name in cases:
            ret = run_case(case_name, args.host, args.user, args.password,
                           args.duration, args.json, args.html, args.history)
            if ret != 0:
                worst = ret
        return worst

    return run_case(args.case, args.host, args.user, args.password,
                    args.duration, args.json, args.html, args.history)


if __name__ == "__main__":
    sys.exit(main())
