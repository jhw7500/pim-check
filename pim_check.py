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
    parser.add_argument("--watch", type=int, default=None, metavar="INTERVAL",
                        help="연속 모니터링 모드 (초 단위 간격, 예: --watch 300)")
    parser.add_argument("--tag", type=str, default=None, help="태그 필터 (예: --tag smoke)")
    parser.add_argument("--webhook", type=str, default=None, help="FAIL 시 알림 URL")
    parser.add_argument("--export-csv", action="store_true", help="히스토리를 CSV로 내보내기")
    parser.add_argument("--diff-targets", type=str, default=None,
                        help="두 타겟의 edgeconf 비교 (예: --diff-targets 192.168.0.5,192.168.0.6)")
    parser.add_argument("--parallel", action="store_true", help="다수 타겟 병렬 실행")
    parser.add_argument("--targets", type=str, default=None, help="병렬 타겟 목록 (쉼표 구분: 192.168.0.5,192.168.0.6)")
    parser.add_argument("--history-report", action="store_true", help="히스토리 대시보드 HTML 생성")
    parser.add_argument("--validate-schema", action="store_true", help="schema.yaml 유효성 검증")
    parser.add_argument("--generate", action="store_true", help="스키마 기반 테스트 케이스 자동 생성")
    parser.add_argument("--include-generated", action="store_true", help="자동 생성된 케이스도 실행에 포함")
    return parser.parse_args(argv)


def list_cases(include_generated: bool = False, tag: str | None = None) -> list[str]:
    """profiles/cases/*.yaml 글로브로 케이스 목록을 반환한다.

    depends_on이 정의된 케이스는 의존 대상 뒤에 정렬된다.
    """
    import yaml as _yaml
    pattern = os.path.join(PROFILES_DIR, "cases", "*.yaml")
    paths = glob.glob(pattern)
    if include_generated:
        gen_pattern = os.path.join(PROFILES_DIR, "generated", "*.yaml")
        paths.extend(glob.glob(gen_pattern))

    # 케이스 이름 → 경로 매핑
    name_path = {}
    for p in paths:
        name = os.path.splitext(os.path.basename(p))[0]
        name_path[name] = p

    names = sorted(name_path.keys())

    # 태그 필터
    if tag:
        filtered = []
        for name in names:
            with open(name_path[name]) as f:
                data = _yaml.safe_load(f) or {}
            if tag in data.get("tags", []):
                filtered.append(name)
        names = filtered

    # 의존성 정렬 (토폴로지 정렬)
    deps = {}
    for name in names:
        with open(name_path[name]) as f:
            data = _yaml.safe_load(f) or {}
        deps[name] = data.get("depends_on", [])

    sorted_names = []
    visited = set()

    def _visit(n):
        if n in visited:
            return
        visited.add(n)
        for dep in deps.get(n, []):
            if dep in deps:
                _visit(dep)
        sorted_names.append(n)

    for n in names:
        _visit(n)

    return sorted_names


def run_case(case_name, host, user, password, duration, save_json=False,
             save_html=False, save_history=False, webhook_url=None) -> int:
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
        exit_code = 0 if not real_fails else 1

        if webhook_url and exit_code != 0:
            from notifier import send_webhook
            status = "FAIL"
            send_webhook(webhook_url, results, case_name, host, status)

        return exit_code
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
        cases = list_cases(include_generated=args.include_generated, tag=args.tag)
        if cases:
            print("Available cases:")
            for c in cases:
                print(f"  {c}")
        else:
            print("No cases found in profiles/cases/")
        return 0

    if args.export_csv:
        from history import export_csv
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        filepath = export_csv(report_dir)
        print(f"CSV exported: {filepath}")
        return 0

    if args.diff_targets:
        parts = [h.strip() for h in args.diff_targets.split(",")]
        if len(parts) != 2:
            print("ERROR: --diff-targets requires exactly 2 hosts (comma-separated)")
            return 1
        host_a, host_b = parts
        user = args.user or "root"
        password = args.password or "root"
        ssh_a = SshClient(host_a, user, password)
        ssh_b = SshClient(host_b, user, password)
        if not ssh_a.check_connectivity():
            print(f"ERROR: Cannot connect to {host_a}")
            return 1
        if not ssh_b.check_connectivity():
            print(f"ERROR: Cannot connect to {host_b}")
            return 1
        conf_a = ssh_a.run("cat /root/shared_v/edgeconf_pim.json")
        conf_b = ssh_b.run("cat /root/shared_v/edgeconf_pim.json")
        if conf_a is None or conf_b is None:
            print("ERROR: Failed to read edgeconf from one or both targets")
            return 1
        import json
        dict_a = json.loads(conf_a)
        dict_b = json.loads(conf_b)

        def _flat(d, prefix=""):
            items = {}
            for k, v in d.items():
                key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    items.update(_flat(v, key))
                else:
                    items[key] = v
            return items

        flat_a = _flat(dict_a)
        flat_b = _flat(dict_b)
        all_keys = sorted(set(flat_a.keys()) | set(flat_b.keys()))
        diffs = []
        for k in all_keys:
            va = flat_a.get(k, "<missing>")
            vb = flat_b.get(k, "<missing>")
            if va != vb:
                diffs.append((k, va, vb))
        if diffs:
            print(f"edgeconf diff: {host_a} vs {host_b} ({len(diffs)} differences)")
            for k, va, vb in diffs:
                print(f"  {k}: {va} | {vb}")
        else:
            print(f"edgeconf identical: {host_a} vs {host_b}")
        return 0

    if args.history_report:
        from history import save_dashboard
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        filepath = save_dashboard(report_dir)
        print(f"Dashboard saved: {filepath}")
        return 0

    if args.validate_schema:
        from generator import load_schema
        schema_path = os.path.join(PROFILES_DIR, "schema.yaml")
        if not os.path.exists(schema_path):
            print(f"ERROR: {schema_path} not found")
            return 1
        schema = load_schema(schema_path)
        errors = []
        # 필수 키 검사
        if "sources" not in schema:
            errors.append("missing 'sources' section")
        if "generation" not in schema:
            errors.append("missing 'generation' section")
        # sources 검사
        for src_name, src in schema.get("sources", {}).items():
            if "axes" not in src:
                errors.append(f"source '{src_name}': missing 'axes'")
            for axis_name, axis in src.get("axes", {}).items():
                if "combinations" not in axis:
                    errors.append(f"source '{src_name}' axis '{axis_name}': missing 'combinations'")
                for i, combo in enumerate(axis.get("combinations", [])):
                    if "name" not in combo:
                        errors.append(f"source '{src_name}' axis '{axis_name}' combo[{i}]: missing 'name'")
                    if "values" not in combo:
                        errors.append(f"source '{src_name}' axis '{axis_name}' combo[{i}]: missing 'values'")
        # generation groups 검사
        gen = schema.get("generation", {})
        groups = gen.get("groups", [{"source": "edgeconf", "cross": gen.get("cross", []),
                                      "output_dir": gen.get("output_dir", ""),
                                      "filename_pattern": gen.get("filename_pattern", "")}])
        for g in groups:
            src = g.get("source", "")
            if src and src not in schema.get("sources", {}):
                errors.append(f"group '{g.get('name', '?')}': source '{src}' not defined in sources")
            for axis in g.get("cross", []):
                if src in schema.get("sources", {}) and axis not in schema["sources"][src].get("axes", {}):
                    errors.append(f"group '{g.get('name', '?')}': axis '{axis}' not defined in source '{src}'")

        if errors:
            print(f"Schema validation FAILED ({len(errors)} errors):")
            for e in errors:
                print(f"  - {e}")
            return 1
        print(f"Schema validation OK — {len(schema.get('sources', {}))} sources, {sum(len(s.get('axes', {})) for s in schema.get('sources', {}).values())} axes")
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

    if args.parallel:
        from parallel import run_parallel, format_parallel_results, load_targets
        if args.targets:
            hosts = [h.strip() for h in args.targets.split(",")]
        else:
            targets_path = os.path.join(PROFILES_DIR, "targets.yaml")
            target_entries = load_targets(targets_path)
            if not target_entries:
                print("No targets defined. Use --targets or profiles/targets.yaml")
                return 1
            hosts = [t["host"] for t in target_entries]
        # 타겟별 overrides 수집
        target_overrides = {}
        if not args.targets:
            for t in target_entries:
                if "overrides" in t:
                    target_overrides[t["host"]] = t["overrides"]
        results = run_parallel(
            hosts, args.case,
            user=args.user or "root",
            password=args.password or "root",
            duration=args.duration,
            target_overrides=target_overrides or None,
        )
        print(format_parallel_results(results))

        if args.history:
            from history import append_result
            report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
            for r in results:
                if r["results"]:
                    append_result(r["results"], r["case"], r["host"], r["collected"], r["total"], report_dir)

        all_ok = all(r["status"] in ("PASS", "WARN") for r in results)
        return 0 if all_ok else 1

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
        cases = list_cases(include_generated=args.include_generated, tag=args.tag)
        if not cases:
            print("No cases found.")
            return 1
        from color import green, red, bold, dim
        case_results = {}
        worst = 0
        for i, case_name in enumerate(cases, 1):
            print(dim(f"\n[{i}/{len(cases)}] {case_name}"))
            ret = run_case(case_name, args.host, args.user, args.password,
                           args.duration, args.json, args.html, args.history, args.webhook)
            case_results[case_name] = "PASS" if ret == 0 else "FAIL"
            if ret != 0:
                worst = ret

        # 요약 테이블
        print(bold("\n=== Summary ==="))
        ok = sum(1 for v in case_results.values() if v == "PASS")
        total = len(case_results)
        for name, status in case_results.items():
            if status == "PASS":
                print(f"  {green('[+]')} {name}: {green('PASS')}")
            else:
                print(f"  {red('[X]')} {name}: {red('FAIL')}")
        color_fn = green if ok == total else red
        print(f"\n{bold('Total:')} {color_fn(f'{ok}/{total}')} cases passed")
        return worst

    # --watch: 연속 모니터링
    if args.watch:
        import time as _time
        interval = args.watch
        case = args.case
        print(f"Watch mode: running every {interval}s (Ctrl+C to stop)")
        try:
            while True:
                run_case(case, args.host, args.user, args.password,
                         args.duration, args.json, args.html, True, args.webhook)
                # 매 실행 후 대시보드 자동 갱신
                from history import save_dashboard
                report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
                save_dashboard(report_dir)
                print(f"\n--- Next run in {interval}s (dashboard updated) ---\n")
                _time.sleep(interval)
        except KeyboardInterrupt:
            print("\nWatch mode stopped.")
            return 0

    return run_case(args.case, args.host, args.user, args.password,
                    args.duration, args.json, args.html, args.history, args.webhook)


if __name__ == "__main__":
    sys.exit(main())
