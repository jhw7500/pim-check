#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import os
import signal
import sys


def _install_graceful_exit_handlers() -> None:
    """SIGTERM/SIGINT 수신 시 KeyboardInterrupt로 변환하여 finally 블록의
    teardown이 동작하도록 한다. 강제 종료 시 보드 conf 잔재로 인한 reboot
    loop 방지 목적 (보드 fw chk_cam_operate.sh가 final_dir stall 감지 시
    reboot escalation 트리거).

    SIGKILL은 OS 레벨이라 handler로 잡을 수 없음 — 사용자는 `kill -TERM`
    또는 Ctrl+C 사용 권장.
    """
    def _handler(signum, _frame):
        sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        raise KeyboardInterrupt(f"{sig_name} received — running teardown")

    signal.signal(signal.SIGTERM, _handler)
    # SIGINT는 Python 기본 KeyboardInterrupt지만 명시 등록으로 일관성 유지
    signal.signal(signal.SIGINT, _handler)

from checks.custom import item_results as _custom_item_results
from config import load_profile
from engine import Engine
from reporter import Reporter
from setup import SetupManager
from ssh import SshClient
from verify_retry import run_verify_with_retry

PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="pim-check — iMX8MP 타겟 QA 자동화 툴"
    )
    parser.add_argument("--version", action="version", version="pim-check 2.0.0")
    parser.add_argument("--quiet", action="store_true", help="출력 최소화 (exit code만)")
    parser.add_argument("--case", type=str, default=None, help="실행할 테스트 케이스 이름")
    parser.add_argument("--all", action="store_true", help="모든 케이스 실행")
    parser.add_argument("--host", type=str, default=None, help="타겟 IP 주소")
    parser.add_argument("--user", type=str, default=None, help="SSH 유저")
    parser.add_argument("--password", type=str, default=None, help="SSH 비밀번호")
    parser.add_argument("--duration", type=int, default=None, help="모니터 duration 오버라이드 (초)")
    parser.add_argument("--list", action="store_true", help="사용 가능한 케이스 목록 출력")
    parser.add_argument("--learn", action="store_true", help="베이스라인 학습 모드")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 파일로 저장")
    parser.add_argument("--junit", action="store_true", help="JUnit XML 리포트 저장 (CI용)")
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
    parser.add_argument("--log", action="store_true", help="실행 로그를 파일에 저장")
    parser.add_argument("--init-config", action="store_true", help="~/.pim-check.yaml 기본 설정 생성")
    parser.add_argument("--compare", action="store_true", help="최근 두 실행 결과 비교")
    parser.add_argument("--parallel", action="store_true", help="다수 타겟 병렬 실행")
    parser.add_argument("--targets", type=str, default=None, help="병렬 타겟 목록 (쉼표 구분: 192.168.0.5,192.168.0.6)")
    parser.add_argument("--history-report", action="store_true", help="히스토리 대시보드 HTML 생성")
    parser.add_argument("--validate-schema", action="store_true", help="schema.yaml 유효성 검증")
    parser.add_argument("--generate", action="store_true", help="스키마 기반 테스트 케이스 자동 생성")
    parser.add_argument("--include-generated", action="store_true", help="자동 생성된 케이스도 실행에 포함")
    parser.add_argument("--plan", type=str, default=None,
                        help="실행할 plan 이름 (profiles/plans/{name}.yaml)")
    parser.add_argument("--list-plans", action="store_true",
                        help="사용 가능한 plan 목록 출력")
    parser.add_argument("--promote-baseline", type=str, default=None, metavar="PLAN",
                        help="plan 결과를 baseline으로 promote (예: --promote-baseline comprehensive)")
    parser.add_argument("--baseline-source", type=str, default=None, metavar="PATH",
                        help="promote 대상 결과 JSON 경로 (생략 시 가장 최근)")
    parser.add_argument("--baseline-label", type=str, default=None, metavar="LABEL",
                        help="baseline 파일 라벨 (예: v1_2). 생략 시 source 파일명 그대로")
    args = parser.parse_args(argv)
    # --password 미지정 시 PIM_PASSWORD 환경변수로 대체 — 비밀번호를 argv(ps/proc 노출)
    # 대신 env 로 전달하기 위함(웹 제어판 spawn 경로에서 사용). 모든 다운스트림
    # args.password 사용처가 자동으로 env 값을 받는다.
    if args.password is None:
        env_pw = os.environ.get("PIM_PASSWORD")
        if env_pw:
            args.password = env_pw
    return args


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


def _promote_baseline(args) -> int:
    """plan 결과 JSON을 baseline으로 promote.

    Source 결정: --baseline-source 명시 또는 reports/{plan}/*.json 중 가장 최근.
    Target: reports/{plan}/baselines/{label}.json (label은 --baseline-label 또는 source 파일명).
    """
    from plan import find_latest_report, promote_baseline as _promote
    plan_name = args.promote_baseline
    project_root = os.path.dirname(os.path.abspath(__file__))

    if args.baseline_source:
        source = args.baseline_source
        if not os.path.isabs(source):
            source = os.path.join(project_root, source)
    else:
        source = find_latest_report(plan_name, project_root)
        if source is None:
            print(f"ERROR: reports/{plan_name}/ 에 결과 JSON이 없습니다.")
            print(f"  먼저 'python3 pim_check.py --plan {plan_name}'을 실행하세요.")
            return 3

    try:
        target = _promote(source, plan_name, project_root,
                          label=args.baseline_label)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 3

    rel_target = os.path.relpath(target, project_root)
    print(f"Promoted: {os.path.relpath(source, project_root)}")
    print(f"      → {rel_target}")
    print()
    print("plan의 gate.baseline_ref.file을 다음 경로로 갱신하세요:")
    print(f"  {rel_target}")
    return 0


def _run_plan(args) -> int:
    """--plan {name} 실행 — plan-driven case 순차 실행 + gate 평가 + reports 출력.

    Phase 3 통합: load_plan → execute_plan → load_baseline → evaluate_gate → render_reports.
    Exit code: 0=PASS, 1=FAIL, 2=WARN, 3=plan lint/실행 에러.
    """
    from plan import (
        load_plan, execute_plan, load_baseline, evaluate_gate, render_reports,
    )
    from setup import SetupManager
    from engine import Engine

    project_root = os.path.dirname(os.path.abspath(__file__))
    plan_path = os.path.join(PROFILES_DIR, "plans", f"{args.plan}.yaml")
    if not os.path.exists(plan_path):
        print(f"ERROR: Plan not found: {plan_path}")
        return 3

    try:
        plan = load_plan(plan_path)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 3

    # CLI 오버라이드 dict 구성
    cli_args: dict = {}
    if args.host or args.user or args.password:
        cli_args["target"] = {}
        if args.host:
            cli_args["target"]["host"] = args.host
        if args.user:
            cli_args["target"]["user"] = args.user
        if args.password:
            cli_args["target"]["password"] = args.password
    if args.duration is not None:
        cli_args["monitor"] = {"duration_sec": args.duration}

    host_for_meta = (args.host
                     or plan.gate.get("baseline_ref", {}).get("host", "")
                     or "192.168.0.5")

    # baseline 로드 (있으면)
    baseline = None
    baseline_warning = None
    baseline_ref = plan.gate.get("baseline_ref")
    if baseline_ref:
        baseline, baseline_warning = load_baseline(baseline_ref, project_root)
        if baseline_warning and not args.quiet:
            print(f"WARNING: baseline — {baseline_warning}")

    if not args.quiet:
        print(f"Plan: {plan.name}")
        print(f"  description: {plan.description}")
        print(f"  execution: stop_on_fail={plan.execution['stop_on_fail']}, "
              f"case_retry={plan.execution['case_retry']}")
        if baseline:
            print(f"  baseline: {baseline_ref.get('file')} "
                  f"({len(baseline.get('executions', []))} prior cases)")
        print()

    # --- 실시간 JSONL 이벤트 스트림 (best-effort; 실패해도 plan 실행 불변) ---
    # 기존 *_results.json 출력/CLI 동작은 그대로 두고, 관측 레이어만 덧붙인다.
    from event_session import EventSession
    from plan import resolve_cases as _resolve_cases
    import datetime as _dt

    run_id = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    board = host_for_meta

    def _safe(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None

    try:
        _all_cases = [c for _sec, c in _resolve_cases(plan, PROFILES_DIR)]
    except Exception:
        _all_cases = []

    _stats = {"completed": 0, "pass": 0, "fail": 0, "dur": 0.0}
    _cur = {"case": None}  # 현재 실행 중 case — 실시간 fail 이벤트 컨텍스트용.

    def _first_fail_reason(results):
        for r in results or []:
            if not r.get("passed") and "known_issue" not in r:
                return r.get("reason")
        return None

    sess = None
    try:
        sess = EventSession(run_id, args.plan, board)
        sess.__enter__()
    except Exception:
        sess = None

    def _plan_summary(case_name):
        """케이스 설명 + 검증 항목(custom_commands) 추출 (best-effort)."""
        try:
            prof = load_profile(PROFILES_DIR, case=case_name)
        except Exception:
            return None, None
        desc = prof.get("description") or prof.get("name")
        items = []
        for c in ((prof.get("checks") or {}).get("custom_commands") or []):
            exp = c.get("expected")
            if exp is None and c.get("expected_min") is not None:
                exp = f">= {c.get('expected_min')}"
            items.append({
                "name": c.get("name", "unnamed"),
                "command": c.get("command", ""),
                "expected": exp,
            })
        return desc, (items or None)

    def on_case_start(idx, total, case_name, section):
        _cur["case"] = case_name
        if sess is None:
            return
        desc, checklist = _plan_summary(case_name)
        _safe(sess.emit_case_start, case_name, "collect", desc, checklist)

    def _engine_factory(ssh, profile):
        eng = Engine(ssh, profile)
        # 실시간 체크 단위 fail 이벤트: validate Fail 순간 즉시 JSONL flush.
        if sess is not None:
            eng.emitter = lambda line: _safe(sess.emit, line)
            eng.emit_context = {
                "run_id": run_id, "plan": args.plan, "board": board,
                "case_name": _cur["case"],
            }
        return eng

    def on_progress(idx, total, case_name, execution):
        # 이벤트 emit 은 quiet 와 무관하게 항상 (관측 레이어).
        if sess is not None:
            _stats["completed"] = idx
            if execution.passed:
                _stats["pass"] += 1
            else:
                _stats["fail"] += 1
            _stats["dur"] += (execution.duration_sec or 0.0)
            avg = _stats["dur"] / max(_stats["completed"], 1)
            reason = None
            if not execution.passed:
                reason = execution.error or _first_fail_reason(execution.results) or "FAIL"
            # 항목별 실측값 추출(custom_commands) — 뷰어 '측정 vs 기대' 표시용.
            checklist_results = None
            for _r in execution.results or []:
                if isinstance(_r, dict) and _r.get("name") == "custom_commands":
                    try:
                        _items = _custom_item_results(_r.get("data") or {})
                    except Exception:
                        _items = []
                    checklist_results = [
                        {"name": _it["name"], "actual": _it["actual"],
                         "passed": _it["passed"]} for _it in _items
                    ] or None
                    break
            _safe(sess.emit_case_end, case_name, "validate",
                  "pass" if execution.passed else "fail",
                  completed_cases=_stats["completed"], pass_count=_stats["pass"],
                  fail_count=_stats["fail"], avg_case_duration_s=round(avg, 2),
                  reason=reason, checklist_results=checklist_results)
        # 콘솔 진행 출력은 quiet 가 아닐 때만.
        if args.quiet:
            return
        mark = "[+]" if execution.passed else "[X]"
        retry_str = f" (retries={execution.retries_used})" if execution.retries_used > 0 else ""
        err_str = f" — {execution.error}" if execution.error else ""
        print(f"  [{idx}/{total}] {mark} [{execution.section}] {case_name} "
              f"({execution.duration_sec}s){retry_str}{err_str}")

    try:
        if sess is not None:
            # 모든 케이스의 설명+체크리스트를 run_start 에 미리 실어, 아직 시작 안 한
            # 대기 케이스도 뷰어에서 검증 항목을 볼 수 있게 한다.
            case_plans = {}
            for _cn in _all_cases:
                _d, _cl = _plan_summary(_cn)
                if _d is not None or _cl is not None:
                    case_plans[_cn] = {"desc": _d, "checklist": _cl}
            _safe(sess.emit_run_start, cases=_all_cases, case_plans=(case_plans or None))
            _safe(sess.start_heartbeat)
        executions = execute_plan(
            plan, PROFILES_DIR,
            ssh_factory=lambda h, u, p: SshClient(h, u, p),
            setup_factory=lambda ssh: SetupManager(ssh),
            engine_factory=_engine_factory,
            cli_args=cli_args or None,
            progress=on_progress,
            on_case_start=on_case_start,
        )
    finally:
        if sess is not None:
            _safe(sess.emit_run_end, completed_cases=_stats["completed"],
                  pass_count=_stats["pass"], fail_count=_stats["fail"])
            _safe(sess.__exit__, None, None, None)

    # Gate 평가
    gate_result = evaluate_gate(plan, executions, baseline=baseline)

    # Reports 출력
    written = render_reports(plan, executions, gate_result,
                             host=host_for_meta, base_path=project_root)

    # 종합 출력
    if not args.quiet:
        print()
        print(f"=== Plan {plan.name} {gate_result.verdict}: pass_rate={gate_result.pass_rate} ===")
        if gate_result.regressions:
            print(f"Regressions ({len(gate_result.regressions)}): {', '.join(gate_result.regressions)}")
        if gate_result.fixed:
            print(f"Fixed ({len(gate_result.fixed)}): {', '.join(gate_result.fixed)}")
        if gate_result.new_cases:
            print(f"New cases ({len(gate_result.new_cases)}): {', '.join(gate_result.new_cases)}")
        if gate_result.known_warns:
            print(f"Known warnings: {len(gate_result.known_warns)}")
        if written:
            print(f"Reports: {len(written)}")
            for p in written:
                print(f"  {p}")
        failed_cases = [e for e in executions if not e.passed]
        if failed_cases:
            print(f"Failed cases ({len(failed_cases)}):")
            for e in failed_cases:
                err = f" ({e.error})" if e.error else ""
                print(f"  [{e.section}] {e.case_name}{err}")

    # Exit code 매핑
    verdict_to_exit = {"PASS": 0, "FAIL": 1, "WARN": 2}
    return verdict_to_exit.get(gate_result.verdict, 1)


def run_case(case_name, host, user, password, duration, save_json=False,
             save_html=False, save_history=False, webhook_url=None,
             quiet=False, save_junit=False) -> int:
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

    if not quiet:
        print(f"Connecting to {host}...")

    if not ssh.check_connectivity():
        if not quiet:
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

        results, collected, total = run_verify_with_retry(
            engine, ssh, effective_duration,
            log=(None if quiet else print),
        )

        reporter = Reporter()
        known_issues = profile.get("known_issues")
        if not quiet:
            print(reporter.format(results, case_name, collected, total, known_issues=known_issues))

        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

        if save_json:
            filepath = reporter.save_json(results, case_name, host, collected, total, report_dir)
            if not quiet:
                print(f"JSON report saved: {filepath}")

        if save_html:
            from html_reporter import save_html as _save_html
            filepath = _save_html(results, case_name, host, collected, total, report_dir)
            if not quiet:
                print(f"HTML report saved: {filepath}")

        if save_junit:
            from junit_reporter import save_junit_xml
            filepath = save_junit_xml(results, case_name, host, collected, total, report_dir)
            if not quiet:
                print(f"JUnit XML saved: {filepath}")

        if save_history:
            from history import append_result
            append_result(results, case_name, host, collected, total, report_dir)

        # known_issue가 붙은 FAIL은 실패로 치지 않음
        real_fails = [r for r in results if not r["passed"] and "known_issue" not in r]
        exit_code = 0 if not real_fails else 1

        if webhook_url and exit_code != 0:
            from notifier import send_webhook
            send_webhook(webhook_url, results, case_name, host, "FAIL")

        # 이메일 알림 (user_config에 email 설정이 있을 때)
        if exit_code != 0:
            from user_config import load_user_config
            cfg = load_user_config()
            email_cfg = cfg.get("email")
            if email_cfg and email_cfg.get("sender"):
                from notifier_email import send_fail_email
                send_fail_email(email_cfg, results, case_name, host)

        return exit_code
    finally:
        # Teardown: setup이 실제로 변경한 경우에만 복원
        if setup_config and setup_changed:
            try:
                setup_mgr.run_teardown(setup_config)
            except TimeoutError as e:
                print(f"WARNING: Teardown failed - {e}")


def main(argv=None) -> int:
    _install_graceful_exit_handlers()
    args = parse_args(argv)

    # 사용자 설정 파일 로드 + CLI 기본값 적용
    from user_config import load_user_config, apply_defaults, init_user_config
    user_cfg = load_user_config()
    apply_defaults(args, user_cfg)

    if args.init_config:
        path = init_user_config()
        print(f"Config created: {path}")
        return 0

    if args.compare:
        from compare import compare_runs, format_comparison
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        result = compare_runs(report_dir, case_filter=args.case)
        print(format_comparison(result))
        return 0

    # 로그 파일 출력
    _logger = None
    if args.log or user_cfg.get("log_enabled"):
        from logger import FileLogger
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "logs")
        _logger = FileLogger(log_dir)
        _logger.__enter__()

    try:
        return _main_run(args)
    finally:
        if _logger:
            print(f"\nLog saved: {_logger.filepath}")
            _logger.__exit__(None, None, None)


def _main_run(args) -> int:
    if args.list:
        cases = list_cases(include_generated=args.include_generated, tag=args.tag)
        if cases:
            print("Available cases:")
            for c in cases:
                print(f"  {c}")
        else:
            print("No cases found in profiles/cases/")
        return 0

    if args.list_plans:
        from plan import list_plans
        plans = list_plans(PROFILES_DIR)
        if plans:
            print("Available plans:")
            for p in plans:
                print(f"  {p}")
        else:
            print("No plans found in profiles/plans/")
        return 0

    if args.plan:
        return _run_plan(args)

    if args.promote_baseline:
        return _promote_baseline(args)

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
                           args.duration, args.json, args.html, args.history,
                           args.webhook, args.quiet, args.junit)
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
                         args.duration, args.json, args.html, True, args.webhook,
                         args.quiet, args.junit)
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
                    args.duration, args.json, args.html, args.history,
                    args.webhook, args.quiet, args.junit)


if __name__ == "__main__":
    sys.exit(main())
