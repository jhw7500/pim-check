"""
plan.py — Declarative Release Plan loader, linter, resolver, executor, gate evaluator.

Phase 1: Plan/GateResult dataclass + load_plan + lint_plan + resolve_cases + list_plans
Phase 2: resolve_runtime_profile + execute_plan (per-case loop)
Phase 3 (NEW): evaluate_gate + load_baseline (TTL warn) + render_reports

Design doc: ~/.gstack/projects/jhw7500-pim-check/jhw-main-design-20260430-130751.md
"""
from __future__ import annotations

import copy
import fnmatch
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import yaml


# ── 데이터 모델 ────────────────────────────────────────────────

DEFAULT_EXECUTION: dict[str, Any] = {
    "stop_on_fail": False,
    "case_retry": 0,
    "retry_wait_sec": 0,
    "reboot_wait_sec": 300,
    "wait_between_cases": 0,
    # plan-level 모니터 상한(초). 설정 시 각 case 의 monitor.duration_sec 을 이 값으로
    # cap(min) 한다 — case 가 명시한 0(snapshot)은 그대로 0 유지(덮어쓰지 않음).
    # smoke 같은 sanity gate 에서 카메라 case 모니터를 짧게. None 이면 cap 없음(기존 동작).
    "monitor_cap_sec": None,
    # finalize-aware: True 면 monitor 가 전 체크 통과 스냅샷에서 조기 종료(duration 은 상한).
    # 카메라 case 의 "부팅 후 finalize 2개" 준비 즉시 끝내 단축. 기본 False(comprehensive 는
    # 전체 지속검증 유지). cap 과 달리 검증을 깨지 않는다(통과해야 종료하므로).
    "monitor_until_pass": False,
}

DEFAULT_GATE: dict[str, Any] = {
    "threshold_pass_rate": 1.0,
    "allow_known_issue": True,
}

ALLOWED_REPORT_FORMATS = {"json", "html", "junit", "markdown_summary"}
ALLOWED_NEW_CASE_POLICIES = {"warn", "skip", "fail"}
SCHEMA_VERSION = 1


@dataclass
class Plan:
    """선언적 릴리스 플랜.

    필드는 plan YAML과 1:1 매핑. resolve_runtime_profile은 plan.execution을 머지 대상에서
    제외하고 외곽 루프(execute_plan)가 직접 사용한다.
    """
    name: str
    description: str
    version: int
    cases: dict[str, list]              # {"regression": [...], "delta": [...]}
    execution: dict[str, Any]
    gate: dict[str, Any]
    reports: list[dict[str, Any]]
    source_path: str = ""


@dataclass
class GateResult:
    """Gate 평가 결과. evaluate_gate가 반환하며 v1.1에서 구현."""
    verdict: str = "PASS"               # "PASS" | "FAIL" | "WARN"
    pass_rate: float = 0.0
    regressions: list = field(default_factory=list)
    fixed: list = field(default_factory=list)
    new_cases: list = field(default_factory=list)
    known_warns: list = field(default_factory=list)


# ── Linting ──────────────────────────────────────────────────────

# v1.1로 미루기로 결정된 키 — lint에서 명시적 메시지로 reject
DEPRECATED_KEYS = {
    "gate.mode": (
        "gate.mode는 v3 스펙에서 제거되었습니다. "
        "threshold_pass_rate(0.0~1.0) + allow_known_issue + baseline_ref.fail_on_new_failure로 대체."
    ),
}

# v1에서 미지원, v1.1로 미룬 selector 키
UNSUPPORTED_SELECTOR_KEYS = {
    "tag": (
        "tag selector는 v1.1로 미루어졌습니다. "
        "v1에서는 case name(정확 일치) 또는 glob(* 와일드카드)만 사용하세요. "
        "예: '720p_2ch' 또는 'hflip_*'"
    ),
}


def lint_plan(plan_dict: dict[str, Any]) -> list[str]:
    """plan dict를 검증하여 사람이 읽을 수 있는 에러 메시지 리스트를 반환한다.

    빈 리스트면 PASS. 비어 있지 않으면 load_plan이 ValueError로 raise.

    검증 항목:
      - 필수 키 (name, description, version, cases, gate)
      - 타입 (name=str, version=int, cases=dict, gate=dict, reports=list)
      - cases 섹션에 최소 하나 이상의 selector 항목
      - cases selector가 v1 지원 형식인지 (str = name/glob, dict = 미지원)
      - gate.threshold_pass_rate 범위 (0.0~1.0)
      - gate.baseline_ref가 있으면 file/fail_on_new_failure/new_case_policy 검증
      - reports[*].format이 ALLOWED_REPORT_FORMATS에 속하는지
      - deprecated 키 reject
      - schema version 호환성
    """
    errors: list[str] = []

    if not isinstance(plan_dict, dict):
        return [f"plan은 dict이어야 합니다. 받은 타입: {type(plan_dict).__name__}"]

    # 필수 키
    required = ["name", "description", "version", "cases", "gate"]
    for key in required:
        if key not in plan_dict:
            errors.append(f"필수 키 누락: '{key}'")

    # 조기 종료: 필수 키 누락 시 이후 검증 스킵
    if errors:
        return errors

    # name / description
    if not isinstance(plan_dict["name"], str) or not plan_dict["name"].strip():
        errors.append("'name'은 비어있지 않은 문자열이어야 합니다.")
    if not isinstance(plan_dict["description"], str):
        errors.append("'description'은 문자열이어야 합니다.")

    # version
    version = plan_dict["version"]
    if not isinstance(version, int):
        errors.append(f"'version'은 정수여야 합니다. 받은 값: {version!r}")
    elif version != SCHEMA_VERSION:
        errors.append(
            f"지원되지 않는 schema version: {version}. 현재 지원: {SCHEMA_VERSION}"
        )

    # cases
    cases = plan_dict["cases"]
    if not isinstance(cases, dict):
        errors.append(f"'cases'는 dict이어야 합니다. 받은 타입: {type(cases).__name__}")
    else:
        valid_sections = {"regression", "delta"}
        unknown_sections = set(cases.keys()) - valid_sections
        if unknown_sections:
            errors.append(
                f"알 수 없는 cases 섹션: {sorted(unknown_sections)}. "
                f"허용: {sorted(valid_sections)}"
            )
        # 최소 하나의 섹션이 비어있지 않아야 함
        non_empty = [s for s in valid_sections
                     if isinstance(cases.get(s), list) and len(cases[s]) > 0]
        if not non_empty:
            errors.append(
                "cases.regression 또는 cases.delta 중 최소 하나는 비어있지 않은 리스트여야 합니다."
            )
        # 각 selector 항목 검증
        for section in valid_sections:
            section_value = cases.get(section)
            if section_value is None:
                continue
            if not isinstance(section_value, list):
                errors.append(
                    f"cases.{section}은 리스트여야 합니다. 받은 타입: {type(section_value).__name__}"
                )
                continue
            for idx, sel in enumerate(section_value):
                err = _lint_selector(sel, f"cases.{section}[{idx}]")
                if err:
                    errors.append(err)

    # gate
    gate = plan_dict["gate"]
    if not isinstance(gate, dict):
        errors.append(f"'gate'는 dict이어야 합니다. 받은 타입: {type(gate).__name__}")
    else:
        # deprecated 키
        for dep_key, msg in DEPRECATED_KEYS.items():
            top, sub = dep_key.split(".", 1)
            if top == "gate" and sub in gate:
                errors.append(f"deprecated 키 사용 금지: {dep_key} — {msg}")

        # threshold_pass_rate
        threshold = gate.get("threshold_pass_rate", 1.0)
        if not isinstance(threshold, (int, float)):
            errors.append(
                f"gate.threshold_pass_rate는 숫자여야 합니다. 받은 값: {threshold!r}"
            )
        elif not (0.0 <= float(threshold) <= 1.0):
            errors.append(
                f"gate.threshold_pass_rate는 0.0~1.0 범위여야 합니다. 받은 값: {threshold}"
            )

        # allow_known_issue
        if "allow_known_issue" in gate and not isinstance(gate["allow_known_issue"], bool):
            errors.append("gate.allow_known_issue는 bool이어야 합니다.")

        # baseline_ref
        if "baseline_ref" in gate:
            baseline_ref = gate["baseline_ref"]
            if not isinstance(baseline_ref, dict):
                errors.append("gate.baseline_ref는 dict이어야 합니다.")
            else:
                if "file" not in baseline_ref:
                    errors.append("gate.baseline_ref.file 필수.")
                elif not isinstance(baseline_ref["file"], str):
                    errors.append("gate.baseline_ref.file은 문자열이어야 합니다.")
                if "fail_on_new_failure" in baseline_ref \
                        and not isinstance(baseline_ref["fail_on_new_failure"], bool):
                    errors.append("gate.baseline_ref.fail_on_new_failure는 bool이어야 합니다.")
                policy = baseline_ref.get("new_case_policy", "warn")
                if policy not in ALLOWED_NEW_CASE_POLICIES:
                    errors.append(
                        f"gate.baseline_ref.new_case_policy: '{policy}' 미지원. "
                        f"허용: {sorted(ALLOWED_NEW_CASE_POLICIES)}"
                    )

    # execution (옵션)
    if "execution" in plan_dict:
        exec_cfg = plan_dict["execution"]
        if not isinstance(exec_cfg, dict):
            errors.append("'execution'은 dict이어야 합니다.")
        else:
            for key in ("stop_on_fail", "monitor_until_pass"):
                if key in exec_cfg and not isinstance(exec_cfg[key], bool):
                    errors.append(f"execution.{key}은 bool이어야 합니다.")
            for key in ("case_retry", "retry_wait_sec",
                        "reboot_wait_sec", "wait_between_cases"):
                if key in exec_cfg and not isinstance(exec_cfg[key], int):
                    errors.append(f"execution.{key}은 정수여야 합니다.")
                elif key in exec_cfg and exec_cfg[key] < 0:
                    errors.append(f"execution.{key}은 0 이상이어야 합니다.")
            # monitor_cap_sec: None(미사용) 또는 0 이상 정수 (bool 제외)
            cap = exec_cfg.get("monitor_cap_sec")
            if cap is not None:
                if isinstance(cap, bool) or not isinstance(cap, int):
                    errors.append("execution.monitor_cap_sec은 정수 또는 null이어야 합니다.")
                elif cap < 0:
                    errors.append("execution.monitor_cap_sec은 0 이상이어야 합니다.")

    # reports (옵션)
    if "reports" in plan_dict:
        reports = plan_dict["reports"]
        if not isinstance(reports, list):
            errors.append("'reports'는 리스트여야 합니다.")
        else:
            for idx, report in enumerate(reports):
                if not isinstance(report, dict):
                    errors.append(f"reports[{idx}]는 dict이어야 합니다.")
                    continue
                fmt = report.get("format")
                if fmt not in ALLOWED_REPORT_FORMATS:
                    errors.append(
                        f"reports[{idx}].format: '{fmt}' 미지원. "
                        f"허용: {sorted(ALLOWED_REPORT_FORMATS)}"
                    )
                if "path" not in report:
                    errors.append(f"reports[{idx}].path 필수.")
                elif not isinstance(report["path"], str):
                    errors.append(f"reports[{idx}].path는 문자열이어야 합니다.")

    return errors


def _lint_selector(sel: Any, location: str) -> str | None:
    """단일 case selector 항목 검증.

    v1 허용: str (name 정확 일치 또는 glob 패턴)
    v1 거부: dict (예: {tag: smoke}는 v1.1로 미루어짐)
    """
    if isinstance(sel, str):
        if not sel.strip():
            return f"{location}: 빈 문자열 selector 불허."
        return None
    if isinstance(sel, dict):
        # tag 같은 v1.1 selector 명시적 메시지
        for key in sel.keys():
            if key in UNSUPPORTED_SELECTOR_KEYS:
                return f"{location}: {UNSUPPORTED_SELECTOR_KEYS[key]}"
        return f"{location}: dict selector는 v1에서 미지원. 받은 키: {sorted(sel.keys())}"
    return f"{location}: selector는 문자열이어야 합니다. 받은 타입: {type(sel).__name__}"


# ── Loading ──────────────────────────────────────────────────────

def load_plan(plan_path: str) -> Plan:
    """plan YAML 파일을 로드하고 lint한 후 Plan 인스턴스 반환.

    Args:
        plan_path: profiles/plans/{name}.yaml 경로 (절대 또는 상대)

    Returns:
        검증된 Plan 인스턴스. execution/reports에는 기본값이 채워짐.

    Raises:
        FileNotFoundError: 파일이 없을 때
        ValueError: lint_plan이 에러를 반환할 때 (메시지에 모든 에러 포함)
    """
    if not os.path.exists(plan_path):
        raise FileNotFoundError(f"Plan 파일이 없습니다: {plan_path}")

    with open(plan_path, "r") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Plan 파일이 비어있습니다: {plan_path}")

    errors = lint_plan(raw)
    if errors:
        msg = "\n  - ".join(["Plan lint 실패:"] + errors)
        raise ValueError(msg)

    # 기본값 채우기 (lint 통과 후이므로 안전)
    execution = {**DEFAULT_EXECUTION, **raw.get("execution", {})}
    gate = {**DEFAULT_GATE, **raw["gate"]}

    return Plan(
        name=raw["name"],
        description=raw["description"],
        version=raw["version"],
        cases=raw["cases"],
        execution=execution,
        gate=gate,
        reports=raw.get("reports", []),
        source_path=os.path.abspath(plan_path),
    )


def list_plans(profiles_dir: str) -> list[str]:
    """profiles/plans/*.yaml 파일명을 정렬된 리스트로 반환 (확장자 제외)."""
    plans_dir = os.path.join(profiles_dir, "plans")
    if not os.path.isdir(plans_dir):
        return []
    names = []
    for fn in sorted(os.listdir(plans_dir)):
        if fn.endswith(".yaml") and not fn.startswith("_"):
            names.append(fn[:-5])
    return names


# ── Case Resolution ──────────────────────────────────────────────

def resolve_cases(plan: Plan, profiles_dir: str) -> list[tuple[str, str]]:
    """plan의 case selector(name/glob)를 실제 case name 리스트로 확장.

    반환: [(section, case_name)] — section은 "regression" 또는 "delta".
    section은 결과 라벨링/리포트용. baseline diff은 case_name 단위로만 동작.

    알고리즘:
      1. 모든 case 후보 수집: cases/*.yaml + generated/*.yaml (확장자 제거)
      2. regression 섹션 selector 순회 → name 정확 일치 또는 fnmatch glob
      3. delta 섹션 동일하게 확장
      4. case_name 단위 차집합: regression name set과 겹치는 delta 항목 제거
      5. 각 섹션 내부 중복 제거 (첫 등장 우선)

    매칭 안 되는 selector는 lint 단계에선 잡지 못하지만 (case 디렉토리 의존),
    여기서 빈 매칭이면 ValueError로 명시적 실패시킨다.

    Args:
        plan: Plan 인스턴스
        profiles_dir: base.yaml과 cases/ 디렉토리가 있는 경로

    Returns:
        [(section, case_name)] 리스트. 정렬 보장: regression 먼저, 각 섹션은
        selector 정의 순서 + 같은 selector 안에서는 name 알파벳 순.

    Raises:
        ValueError: selector 하나라도 빈 매칭이면 어느 selector인지 표시.
    """
    available = _collect_available_cases(profiles_dir)

    regression_names: list[str] = []
    delta_names: list[str] = []
    unmatched: list[str] = []

    for sel in plan.cases.get("regression", []) or []:
        matched = _match_selector(sel, available)
        if not matched:
            unmatched.append(f"cases.regression: '{sel}'")
        for name in matched:
            if name not in regression_names:
                regression_names.append(name)

    regression_set = set(regression_names)

    for sel in plan.cases.get("delta", []) or []:
        matched = _match_selector(sel, available)
        if not matched:
            unmatched.append(f"cases.delta: '{sel}'")
        for name in matched:
            if name in regression_set:
                continue  # delta 차집합: regression에 이미 있으면 제외
            if name not in delta_names:
                delta_names.append(name)

    if unmatched:
        msg = "\n  - ".join(
            ["다음 selector가 어떤 case와도 매칭되지 않았습니다:"] + unmatched
        )
        raise ValueError(msg)

    result = [("regression", n) for n in regression_names]
    result += [("delta", n) for n in delta_names]
    return result


def _collect_available_cases(profiles_dir: str) -> list[str]:
    """cases/*.yaml + generated/*.yaml 의 stem 이름을 알파벳 정렬로 반환."""
    candidates = set()
    for sub in ("cases", "generated"):
        sub_path = os.path.join(profiles_dir, sub)
        if not os.path.isdir(sub_path):
            continue
        for fn in os.listdir(sub_path):
            if fn.endswith(".yaml") and not fn.startswith("_"):
                candidates.add(fn[:-5])
    return sorted(candidates)


def _match_selector(sel: str, available: list[str]) -> list[str]:
    """단일 selector(str)를 available case name 리스트와 매칭.

    - "*" 또는 "?" 또는 "[" 문자가 있으면 fnmatch glob으로 처리
    - 아니면 정확 일치로 처리
    - 결과는 알파벳 정렬
    """
    if any(c in sel for c in "*?["):
        matched = sorted(n for n in available if fnmatch.fnmatch(n, sel))
        return matched
    return [sel] if sel in available else []


# ── Runtime Profile Merge (Phase 2) ───────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """config.deep_merge와 동일 — 외부 의존을 plan.py 자체로 가져옴 (cycle 회피)."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def resolve_runtime_profile(case_profile: dict,
                            plan_global: dict | None = None,
                            cli_args: dict | None = None) -> dict:
    """case_profile (load_profile 결과) 위에 plan-level과 CLI 인자를 우선순위대로 머지.

    우선순위 (낮음 → 높음): case_profile (= base + case YAML 머지) → plan_global → cli_args.

    plan_global은 plan-level case override (v1에서는 미사용, v1.1+ case_overrides용 placeholder).
    cli_args는 dict 형태 — 예: {"target": {"host": "10.0.0.5"}, "monitor": {"duration_sec": 60}}.

    참고: plan.execution은 머지 대상이 아님 (run policy로 execute_plan이 직접 사용).

    Returns: 머지된 새 dict (입력 보존).
    """
    profile = copy.deepcopy(case_profile)
    if plan_global:
        profile = _deep_merge(profile, plan_global)
    if cli_args:
        profile = _deep_merge(profile, cli_args)
    return profile


# ── Plan Execution (Phase 2) ──────────────────────────────────────

@dataclass
class CaseExecution:
    """plan 안에서 단일 case 실행 결과."""
    section: str                    # "regression" | "delta"
    case_name: str
    results: list                   # engine.run_snapshot 결과 리스트
    passed: bool                    # 모든 check pass + known_issue 제외 시 True
    retries_used: int               # plan.execution.case_retry 사용 횟수
    error: str | None = None        # NO_SSH / SETUP_TIMEOUT / EXCEPTION 등
    duration_sec: float = 0.0


def _run_single_case(ssh, profile: dict, case_name: str,
                     engine_factory: Callable,
                     setup_factory: Callable | None,
                     monitor_cap_sec: int | None = None,
                     monitor_until_pass: bool = False,
                     mgr_holder: list | None = None) -> tuple[list, bool, str | None]:
    """단일 case의 setup → engine.run_snapshot → known_issue 처리 한 번 실행.

    Args:
        mgr_holder: 주어지면 이번 case 가 만든 SetupManager 를 여기에 담는다.
            execute_plan 의 finally teardown 이 **같은 인스턴스**를 재사용하기
            위해서다 — SetupManager 는 teardown 복원 원본(_config_snapshots)을
            인스턴스 속성에 들고 있어서, 새로 만들면 그 상태가 비어 복원이 항상
            폴백으로 떨어진다 (pim-check#67).

    Returns: (results, passed, error). error는 None or "NO_SSH"/"SETUP_TIMEOUT"/"EXCEPTION:..."
    """
    if not ssh.check_connectivity():
        return [], False, "NO_SSH"

    # Setup (edgeconf 변경 + reboot, 있으면)
    setup_cfg = profile.get("setup")
    if setup_cfg and setup_factory is not None:
        # 리부트 후 안정화 readiness 주입 (processes / recording / camera_init(fsync)).
        # 추출 로직은 setup.readiness_kwargs 단일 출처 (run_case 와 공용).
        from setup import readiness_kwargs
        try:
            setup_mgr = setup_factory(ssh)
            # 예외로 빠져나가도 teardown 이 이 인스턴스를 쓰도록 먼저 담는다 —
            # run_setup 이 중간에 실패해도 그때까지의 스냅샷은 유효하다.
            if mgr_holder is not None:
                mgr_holder.clear()
                mgr_holder.append(setup_mgr)
            setup_mgr.run_setup(setup_cfg, **readiness_kwargs(profile))
        except TimeoutError as exc:
            return [], False, f"SETUP_TIMEOUT: {exc}"
        except Exception as exc:
            return [], False, f"SETUP_EXCEPTION: {type(exc).__name__}: {exc}"

    # Engine 실행 (run_snapshot 한 번만 — duration<=0 가정. monitor 모드는 v1.1)
    # verify_retry: 안정화 미달(SSH 끊김 / recovering / NEED_2_FINALIZES 등) 시 자동 재시도.
    try:
        engine = engine_factory(ssh, profile)
        from verify_retry import run_verify_with_retry
        effective_duration = (profile.get("monitor") or {}).get("duration_sec", 0) or 0
        # plan-level cap(min): case 가 0(snapshot)이면 0 유지, 긴 모니터만 짧게 자른다.
        if monitor_cap_sec is not None and effective_duration > monitor_cap_sec:
            effective_duration = monitor_cap_sec
        results, _coll, _total = run_verify_with_retry(
            engine, ssh, effective_duration, log=print,
            until_pass=monitor_until_pass,
        )
    except Exception as exc:
        return [], False, f"RUN_EXCEPTION: {type(exc).__name__}: {exc}"

    # known_issues 매칭 — reporter가 in-place로 'known_issue' 키 추가
    known = profile.get("known_issues")
    if known:
        try:
            from reporter import Reporter
            Reporter().format(results, case_name, 1, 1, known_issues=known)
        except Exception:
            pass  # 매칭 실패는 fatal 아님

    # passed = all PASS + known_issue로 분류된 FAIL은 통과 처리
    real_fails = [r for r in results if not r.get("passed") and "known_issue" not in r]
    passed = len(real_fails) == 0
    return results, passed, None


def execute_plan(plan: Plan, profiles_dir: str,
                 ssh_factory: Callable,
                 setup_factory: Callable | None = None,
                 engine_factory: Callable | None = None,
                 cli_args: dict | None = None,
                 progress: Callable | None = None,
                 on_case_start: Callable | None = None) -> list[CaseExecution]:
    """Plan 실행 — resolve_cases 순서대로 case 단위 실행.

    각 case별:
      1. load_profile(profiles_dir, case=case_name) — base + case YAML 머지
      2. resolve_runtime_profile(profile, None, cli_args) — CLI override 적용
      3. ssh_factory(host, user, password) — 새 SshClient
      4. _run_single_case (setup + engine + known_issue)
      5. plan.execution.case_retry까지 재시도
      6. plan.execution.stop_on_fail 평가
      7. plan.execution.wait_between_cases sleep

    Args:
        plan: Plan 인스턴스
        profiles_dir: base.yaml과 cases/ 디렉토리가 있는 경로
        ssh_factory: callable (host, user, password) → SshClient-like
        setup_factory: callable (ssh) → SetupManager-like. None이면 setup 스킵.
        engine_factory: callable (ssh, profile) → Engine-like. None이면 기본 Engine 사용.
        cli_args: dict 형태 CLI 오버라이드 (예: {"target": {"host": "..."}})
        progress: callable (idx, total, case_name, exec_result) — case 끝날 때 호출 (선택)
        on_case_start: callable (idx, total, case_name, section) — case 시작 시 호출 (선택)

    Returns:
        list[CaseExecution] — case별 실행 결과. plan에 정의된 순서.
    """
    # 지연 import — testability (plan.py 자체는 engine 미의존)
    if engine_factory is None:
        from engine import Engine
        engine_factory = Engine

    # config.load_profile 사용 (base + case YAML 머지)
    from config import load_profile

    resolved = resolve_cases(plan, profiles_dir)
    total = len(resolved)
    executions: list[CaseExecution] = []

    # 마지막 case의 ssh + setup_cfg 추적 — plan 종료(정상/예외) 시
    # teardown으로 conf를 case 직전 상태(backup)로 복원하여 보드 fw
    # chk_cam_operate.sh의 stall escalation(→ reboot loop)을 방지.
    last_ssh = None
    last_setup_cfg = None
    # teardown: 섹션(recovery_command)도 같이 들고 간다 — setup 섹션만 넘기면 fault
    # 주입 케이스의 복구가 통째로 누락된다 (pim-check#75).
    last_teardown_cfg = None
    # 마지막 case 의 SetupManager 와 그 짝 ssh — finally teardown 이 **같은 인스턴스**를
    # 재사용하기 위해 추적한다. 새로 만들면 teardown 복원 원본(_config_snapshots)이
    # 빈 채로 시작해 복원이 항상 .bak 폴백으로 떨어진다 (pim-check#67).
    last_setup_mgr = None
    last_mgr_ssh = None
    # 캠페인 복원 (pim-check#68) — 케이스마다 teardown 하지 않으므로, 마지막 케이스의
    # 매니저가 든 스냅샷은 "마지막 케이스 직전" 상태다. 파일별 **최초** 스냅샷을
    # 따로 모아 두고 끝에서 그것으로 되돌린다. 복원 **대상**도 캠페인 기준이어야
    # 한다 — 중간 케이스가 ord_vcm 을 바꾸고 마지막이 edgeconf 만 바꾸면 ord_vcm 이
    # 되돌려지지 않는다(ord_vcm_changes 를 쓰는 케이스가 6건 있다).
    campaign_snapshots: dict[str, str] = {}
    campaign_edge_changes: dict = {}
    campaign_ord_changes: dict = {}

    try:
        for idx, (section, case_name) in enumerate(resolved, 1):
            t0 = time.monotonic()

            # case 시작 훅 (선택) — 이벤트 스트림의 case_start emit 지점.
            if on_case_start is not None:
                on_case_start(idx, total, case_name, section)

            # case profile 로드
            try:
                case_profile = load_profile(profiles_dir, case=case_name)
            except FileNotFoundError as exc:
                executions.append(CaseExecution(
                    section=section, case_name=case_name,
                    results=[], passed=False, retries_used=0,
                    error=f"PROFILE_NOT_FOUND: {exc}",
                    duration_sec=round(time.monotonic() - t0, 2),
                ))
                if plan.execution["stop_on_fail"]:
                    break
                continue

            # CLI 오버라이드 적용
            runtime = resolve_runtime_profile(case_profile,
                                              plan_global=None,
                                              cli_args=cli_args)

            target = runtime.get("target", {})
            host = target.get("host", "192.168.0.5")
            user = target.get("user", "root")
            password = target.get("password", "root")

            # case_retry 루프
            results: list = []
            passed = False
            error: str | None = None
            retries_used = 0
            max_retry = plan.execution["case_retry"]
            for attempt in range(max_retry + 1):
                # 이전 attempt 의 ssh 를 정리 — paramiko persistent transport
                # 가 attempt 사이에 점유되지 않도록. (last_ssh 는 매 attempt
                # 마다 갱신되며 finally 의 teardown 에 쓰일 마지막 인스턴스만 유지.)
                if last_ssh is not None:
                    try:
                        last_ssh.close()
                    except Exception:  # noqa: BLE001 — close 실패는 무시
                        pass
                ssh = ssh_factory(host, user, password)
                # 마지막 활성 ssh + setup_cfg 추적 (finally teardown용)
                last_ssh = ssh
                last_setup_cfg = runtime.get("setup")
                last_teardown_cfg = runtime.get("teardown")
                _mgr_holder: list = []
                results, passed, error = _run_single_case(
                    ssh, runtime, case_name, engine_factory, setup_factory,
                    monitor_cap_sec=plan.execution.get("monitor_cap_sec"),
                    monitor_until_pass=plan.execution.get("monitor_until_pass", False),
                    mgr_holder=_mgr_holder,
                )
                if _mgr_holder:
                    last_setup_mgr = _mgr_holder[0]
                    last_mgr_ssh = ssh
                    # 파일별 **최초** 스냅샷만 남긴다 — 첫 케이스가 그 파일을
                    # 건드리지 않았을 수도 있으므로 "첫 케이스"가 아니라
                    # "그 파일을 처음 건드린 케이스" 기준이다.
                    for _path, _snap in getattr(
                            last_setup_mgr, "_config_snapshots", {}).items():
                        campaign_snapshots.setdefault(_path, _snap)
                _case_setup = runtime.get("setup") or {}
                campaign_edge_changes.update(_case_setup.get("edgeconf_changes") or {})
                campaign_ord_changes.update(_case_setup.get("ord_vcm_changes") or {})
                retries_used = attempt
                if passed:
                    break
                if attempt < max_retry:
                    wait = plan.execution["retry_wait_sec"]
                    if wait > 0:
                        time.sleep(wait)

            execution = CaseExecution(
                section=section, case_name=case_name,
                results=results, passed=passed, retries_used=retries_used,
                error=error,
                duration_sec=round(time.monotonic() - t0, 2),
            )
            executions.append(execution)

            if progress is not None:
                try:
                    progress(idx, total, case_name, execution)
                except Exception:
                    pass  # progress callback 실패는 fatal 아님

            # stop_on_fail
            if not passed and plan.execution["stop_on_fail"]:
                break

            # case 간 인터벌
            wait = plan.execution["wait_between_cases"]
            if wait > 0 and idx < total:
                time.sleep(wait)
    finally:
        # Plan 종료(정상/예외/KeyboardInterrupt) 시 마지막 case 잔재 cleanup.
        # 보드 fw chk_cam_operate.sh의 stall escalation(reboot loop) 방지.
        # `teardown:` 만 둔 케이스도 복구가 도달해야 한다 (pim-check#75 리뷰)
        if (last_ssh is not None and setup_factory is not None
                and (last_setup_cfg or last_teardown_cfg)):
            try:
                # setup 을 돌린 그 매니저를 재사용한다 — 인스턴스 상태(스냅샷)가
                # 이어져야 teardown 복원이 실효한다. ssh 가 갈렸으면(재연결 등)
                # 그 매니저의 세션이 죽었으므로 기존처럼 새로 만든다.
                if last_setup_mgr is not None and last_mgr_ssh is last_ssh:
                    _teardown_mgr = last_setup_mgr
                else:
                    _teardown_mgr = setup_factory(last_ssh)
                # 캠페인 시작 전 상태로 되돌린다 — 매니저가 든 것은 마지막 케이스
                # 직전 상태다. 대상도 캠페인 동안 건드린 파일 전체로 넓힌다.
                if campaign_snapshots:
                    _teardown_mgr.adopt_snapshots(campaign_snapshots)
                # `setup:` 이 없는 케이스는 last_setup_cfg 가 None 이다 — 가드를
                # 넓힌 이상 인자도 방어해야 한다(안 그러면 그 경로가 `.get()` 에서
                # 죽고 아래 except 가 삼켜 조용히 복구가 빠진다).
                _campaign_cfg = dict(last_setup_cfg or {})
                if campaign_edge_changes:
                    _campaign_cfg["edgeconf_changes"] = campaign_edge_changes
                if campaign_ord_changes:
                    _campaign_cfg["ord_vcm_changes"] = campaign_ord_changes
                # `reboot_after` 도 캠페인 단위 결정이다. 마지막 케이스 것을 그대로
                # 쓰면, 마지막이 fault 케이스일 때(setup 에 inject_command 만 있고
                # reboot_after 가 없다) 복원이 **파일만 되돌리고 재부팅을 건너뛴다** —
                # 보드는 앞 케이스 설정으로 계속 돌아 파일과 구동 상태가 어긋난다.
                if campaign_edge_changes or campaign_ord_changes:
                    _campaign_cfg["reboot_after"] = True
                # 캠페인 기준선이 무엇이었는지 남긴다 — 스냅샷이 한 번이라도 실패하면
                # 그 파일은 **다음 케이스의(=이미 변경된) 상태**가 기준선이 되는데,
                # 복원 로그만으로는 그 사실이 드러나지 않는다.
                _teardown_mgr._local0_log(
                    f"teardown CAMPAIGN RESTORE — paths={sorted(campaign_snapshots)}")
                _teardown_mgr.run_teardown(_campaign_cfg, last_teardown_cfg)
            except Exception as _exc:
                print(f"[plan teardown] WARN: cleanup 실패 — {_exc}")
        # paramiko persistent transport 마지막 인스턴스 정리. close() 는 멱등.
        if last_ssh is not None:
            try:
                last_ssh.close()
            except Exception:  # noqa: BLE001
                pass

    return executions


# ── Baseline Loading (Phase 3) ────────────────────────────────────

BASELINE_TTL_DAYS = 30


def load_baseline(baseline_ref: dict, base_path: str) -> tuple[dict | None, str | None]:
    """baseline_ref.file에서 이전 plan 결과 JSON을 로드.

    base_path: 상대경로의 base (보통 project root).

    Returns: (baseline_dict, warning_msg).
      - 파일 없으면 (None, "NO_FILE: ...")
      - mtime이 BASELINE_TTL_DAYS(30일) 초과면 (dict, "STALE: ...일 전")
      - 파싱 실패면 (None, "PARSE_ERROR: ...")
      - 정상이면 (dict, None)
    """
    if not isinstance(baseline_ref, dict):
        return None, "INVALID_REF: baseline_ref가 dict가 아님"
    file_ref = baseline_ref.get("file")
    if not file_ref:
        return None, "NO_FILE_KEY: baseline_ref.file 누락"

    path = file_ref if os.path.isabs(file_ref) else os.path.join(base_path, file_ref)
    if not os.path.exists(path):
        return None, f"NO_FILE: {path}"

    age_sec = time.time() - os.path.getmtime(path)
    age_days = age_sec / 86400
    warning: str | None = None
    if age_days > BASELINE_TTL_DAYS:
        warning = f"STALE: baseline 파일이 {int(age_days)}일 전 (TTL {BASELINE_TTL_DAYS}일 초과)"

    try:
        with open(path, "r") as f:
            baseline = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"PARSE_ERROR: {type(exc).__name__}: {exc}"

    return baseline, warning


def _baseline_case_status(baseline: dict, case_name: str) -> str | None:
    """baseline.executions에서 case_name의 passed 상태 반환. None이면 case 없음."""
    for exe in baseline.get("executions", []):
        if exe.get("case_name") == case_name:
            return "PASS" if exe.get("passed") else "FAIL"
    return None


# ── Gate Evaluation (Phase 3) ─────────────────────────────────────

def evaluate_gate(plan: Plan,
                  executions: list[CaseExecution],
                  baseline: dict | None = None) -> GateResult:
    """Plan + execution 결과를 받아 GateResult 반환.

    알고리즘 (design doc 명시):
      1. 각 case의 known_issue 매칭 결과 수집 → known_warns
      2. baseline_ref가 있으면 case-by-case diff:
         - regressions: 이전 PASS, 이번 FAIL
         - fixed: 이전 FAIL, 이번 PASS
         - new_cases: baseline에 없음
      3. verdict:
         - fail_on_new_failure=true이고 regressions 있으면 → FAIL
         - 그 외 pass_rate < threshold_pass_rate → FAIL
         - 그 외 known_warns 있으면 → WARN, 없으면 → PASS
    """
    gate_cfg = plan.gate
    threshold = float(gate_cfg.get("threshold_pass_rate", 1.0))
    baseline_ref = gate_cfg.get("baseline_ref") or {}
    fail_on_regression = bool(baseline_ref.get("fail_on_new_failure", False))

    regressions: list[str] = []
    fixed: list[str] = []
    new_cases: list[str] = []
    known_warns: list[dict[str, str]] = []
    pass_count = 0

    for exe in executions:
        # known_issue 수집 (results에 'known_issue' 키 있는 항목)
        for r in exe.results:
            if isinstance(r, dict) and "known_issue" in r:
                known_warns.append({
                    "case": exe.case_name,
                    "check": str(r.get("name", "")),
                    "label": str(r.get("known_issue", "")),
                })

        # baseline diff
        if baseline:
            prev = _baseline_case_status(baseline, exe.case_name)
            cur = "PASS" if exe.passed else "FAIL"
            if prev is None:
                new_cases.append(exe.case_name)
            elif prev == "PASS" and cur == "FAIL":
                regressions.append(exe.case_name)
            elif prev == "FAIL" and cur == "PASS":
                fixed.append(exe.case_name)

        if exe.passed:
            pass_count += 1

    total = len(executions)
    pass_rate = (pass_count / total) if total > 0 else 0.0

    # verdict
    if fail_on_regression and regressions:
        verdict = "FAIL"
    elif pass_rate < threshold:
        verdict = "FAIL"
    elif known_warns:
        verdict = "WARN"
    else:
        verdict = "PASS"

    return GateResult(
        verdict=verdict,
        pass_rate=round(pass_rate, 4),
        regressions=regressions,
        fixed=fixed,
        new_cases=new_cases,
        known_warns=known_warns,
    )


# ── Reports Rendering (Phase 3) ───────────────────────────────────

def _execution_to_dict(exe: CaseExecution) -> dict:
    return {
        "section": exe.section,
        "case_name": exe.case_name,
        "passed": exe.passed,
        "retries_used": exe.retries_used,
        "duration_sec": exe.duration_sec,
        "error": exe.error,
        "results": exe.results,
    }


def _gate_result_to_dict(gate: GateResult) -> dict:
    return {
        "verdict": gate.verdict,
        "pass_rate": gate.pass_rate,
        "regressions": gate.regressions,
        "fixed": gate.fixed,
        "new_cases": gate.new_cases,
        "known_warns": gate.known_warns,
    }


def _render_json(plan: Plan, executions: list[CaseExecution],
                 gate: GateResult, host: str, timestamp: str,
                 path: str) -> None:
    payload = {
        "plan": {
            "name": plan.name,
            "description": plan.description,
            "version": plan.version,
        },
        "timestamp": timestamp,
        "host": host,
        "executions": [_execution_to_dict(e) for e in executions],
        "gate": _gate_result_to_dict(gate),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _render_junit(plan: Plan, executions: list[CaseExecution],
                  gate: GateResult, host: str, timestamp: str,
                  path: str) -> None:
    """단순 JUnit XML — case = testcase, plan = testsuite."""
    import xml.etree.ElementTree as ET
    suite = ET.Element("testsuite", {
        "name": plan.name,
        "tests": str(len(executions)),
        "failures": str(sum(1 for e in executions if not e.passed)),
        "timestamp": timestamp,
        "hostname": host,
    })
    for exe in executions:
        tc = ET.SubElement(suite, "testcase", {
            "name": exe.case_name,
            "classname": f"plan.{plan.name}.{exe.section}",
            "time": str(exe.duration_sec),
        })
        if not exe.passed:
            failure_msg = exe.error or "case FAIL"
            failed_checks = [
                f"{r.get('name')}: {r.get('reason', '')}"
                for r in exe.results
                if isinstance(r, dict) and not r.get("passed") and "known_issue" not in r
            ]
            failure = ET.SubElement(tc, "failure", {"message": failure_msg})
            failure.text = "\n".join(failed_checks) or failure_msg
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tree = ET.ElementTree(suite)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _render_html(plan: Plan, executions: list[CaseExecution],
                 gate: GateResult, host: str, timestamp: str,
                 path: str) -> None:
    """단순 HTML summary — plan-level table + verdict + diff sections."""
    rows = []
    for exe in executions:
        cls = "pass" if exe.passed else "fail"
        err_str = f" ({exe.error})" if exe.error else ""
        retry_str = f" retry={exe.retries_used}" if exe.retries_used > 0 else ""
        rows.append(
            f"<tr class='{cls}'><td>{exe.section}</td>"
            f"<td>{exe.case_name}</td>"
            f"<td>{'PASS' if exe.passed else 'FAIL'}{err_str}</td>"
            f"<td>{exe.duration_sec}s{retry_str}</td></tr>"
        )

    diff = ""
    if gate.regressions or gate.fixed or gate.new_cases:
        diff = "<h2>Baseline Diff</h2><ul>"
        if gate.regressions:
            diff += f"<li><b>Regressions ({len(gate.regressions)}):</b> {', '.join(gate.regressions)}</li>"
        if gate.fixed:
            diff += f"<li>Fixed ({len(gate.fixed)}): {', '.join(gate.fixed)}</li>"
        if gate.new_cases:
            diff += f"<li>New cases ({len(gate.new_cases)}): {', '.join(gate.new_cases)}</li>"
        diff += "</ul>"

    known = ""
    if gate.known_warns:
        known = f"<h2>Known Warnings ({len(gate.known_warns)})</h2><ul>"
        for w in gate.known_warns:
            known += f"<li>{w['case']} / {w['check']}: {w['label']}</li>"
        known += "</ul>"

    html = (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{plan.name} — {timestamp}</title>"
        f"<style>body{{font-family:sans-serif;margin:2em}}"
        f"table{{border-collapse:collapse;width:100%}}"
        f"th,td{{border:1px solid #ccc;padding:6px;text-align:left}}"
        f"tr.pass{{background:#e8f5e9}}tr.fail{{background:#ffebee}}"
        f".verdict{{padding:8px;font-weight:bold}}"
        f".v-PASS{{background:#a5d6a7}}.v-FAIL{{background:#ef9a9a}}"
        f".v-WARN{{background:#fff59d}}</style></head><body>"
        f"<h1>{plan.name}</h1><p>{plan.description}</p>"
        f"<p>Host: {host} / Timestamp: {timestamp}</p>"
        f"<div class='verdict v-{gate.verdict}'>VERDICT: {gate.verdict} (pass_rate={gate.pass_rate})</div>"
        f"{diff}{known}"
        f"<h2>Cases ({len(executions)})</h2>"
        f"<table><tr><th>Section</th><th>Case</th><th>Status</th><th>Duration</th></tr>"
        f"{''.join(rows)}</table></body></html>"
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(html)


def _render_markdown_summary(plan: Plan, executions: list[CaseExecution],
                             gate: GateResult, host: str, timestamp: str,
                             path: str) -> None:
    """PR 코멘트/Slack 친화 plan-level summary (단일 markdown).

    포함: verdict, pass_rate, baseline diff (regressions/fixed/new),
         known warnings, failed cases + 실패 reason 요약.
    실패 case 내부의 first 5 fail 항목까지만 표시 (PR 코멘트 길이 제한 대응).
    """
    total = len(executions)
    passed = sum(1 for e in executions if e.passed)
    failed = total - passed

    lines: list[str] = []
    lines.append(f"# {plan.name}")
    lines.append("")
    lines.append(f"**Verdict:** `{gate.verdict}` (pass_rate={gate.pass_rate})")
    lines.append(f"**Cases:** {passed}/{total} passed, {failed} failed")
    lines.append(f"**Host:** `{host}` / **Timestamp:** `{timestamp}`")
    if plan.description:
        lines.append("")
        lines.append(f"> {plan.description.strip()}")
    lines.append("")

    # Baseline diff
    if gate.regressions or gate.fixed or gate.new_cases:
        lines.append("## Baseline Diff")
        if gate.regressions:
            lines.append(f"- **Regressions ({len(gate.regressions)}):** "
                         f"{', '.join(f'`{c}`' for c in gate.regressions)}")
        if gate.fixed:
            lines.append(f"- Fixed ({len(gate.fixed)}): "
                         f"{', '.join(f'`{c}`' for c in gate.fixed)}")
        if gate.new_cases:
            lines.append(f"- New cases ({len(gate.new_cases)}): "
                         f"{', '.join(f'`{c}`' for c in gate.new_cases)}")
        lines.append("")

    # Known warnings
    if gate.known_warns:
        lines.append(f"## Known Warnings ({len(gate.known_warns)})")
        for w in gate.known_warns:
            lines.append(f"- `{w['case']}` / {w['check']}: {w['label']}")
        lines.append("")

    # Failed cases
    failed_list = [e for e in executions if not e.passed]
    if failed_list:
        lines.append(f"## Failed Cases ({len(failed_list)})")
        for e in failed_list:
            err_str = f" — {e.error}" if e.error else ""
            retry_str = f" (retries={e.retries_used})" if e.retries_used > 0 else ""
            lines.append(f"- **`{e.case_name}`** [{e.section}]{err_str}{retry_str}")
            # 실패한 check들 — first 5만
            fail_checks = [
                r for r in e.results
                if isinstance(r, dict) and not r.get("passed") and "known_issue" not in r
            ]
            for r in fail_checks[:5]:
                reason = (r.get("reason") or "")[:100]
                lines.append(f"  - `{r.get('name')}`: {reason}")
            if len(fail_checks) > 5:
                lines.append(f"  - ... +{len(fail_checks) - 5}건 추가 (json/html 리포트 참고)")
        lines.append("")

    if not failed_list and not gate.known_warns:
        lines.append("All cases passed.")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def render_reports(plan: Plan, executions: list[CaseExecution],
                   gate: GateResult, host: str,
                   base_path: str,
                   timestamp: str | None = None) -> list[str]:
    """plan.reports 명세에 따라 html/junit/json/markdown_summary 파일 생성.

    base_path: 상대경로 reports의 base (project root 권장).
    timestamp: 명시 시점 또는 None이면 현재 (YYYYMMDD_HHMMSS).
    Returns: 생성된 파일 절대경로 리스트.
    """
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    written: list[str] = []
    renderers = {
        "json": _render_json,
        "junit": _render_junit,
        "html": _render_html,
        "markdown_summary": _render_markdown_summary,
    }

    for report in plan.reports:
        fmt = report.get("format", "")
        path_template = report.get("path", "")
        if not path_template:
            continue
        path = path_template.replace("{timestamp}", timestamp).replace("{plan_name}", plan.name)
        abs_path = path if os.path.isabs(path) else os.path.join(base_path, path)
        renderer = renderers.get(fmt)
        if renderer is None:
            continue
        try:
            renderer(plan, executions, gate, host, timestamp, abs_path)
            written.append(abs_path)
        except (OSError, ValueError) as exc:
            print(f"WARNING: report '{fmt}' 생성 실패: {exc}")

    return written


# ── Baseline Promotion (Phase 3 — 운영 헬퍼) ─────────────────────

def find_latest_report(plan_name: str, base_path: str) -> str | None:
    """reports/{plan_name}/*.json 중 mtime 가장 최근 파일 경로 반환.

    baselines/ 하위는 제외 — 이미 promote된 baseline은 source가 아니어야 함.
    Returns: 절대경로 또는 None (없을 때).
    """
    reports_dir = os.path.join(base_path, "reports", plan_name)
    if not os.path.isdir(reports_dir):
        return None

    candidates: list[tuple[float, str]] = []
    for entry in os.listdir(reports_dir):
        if not entry.endswith(".json"):
            continue
        full = os.path.join(reports_dir, entry)
        if not os.path.isfile(full):
            continue  # baselines/ 같은 하위 디렉토리 스킵
        try:
            candidates.append((os.path.getmtime(full), full))
        except OSError:
            continue

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def promote_baseline(source_path: str, plan_name: str, base_path: str,
                     label: str | None = None) -> str:
    """source_path JSON을 reports/{plan_name}/baselines/{label}.json으로 복사.

    label 없으면 source 파일명 그대로 사용. .json 확장자 자동 부가.

    Args:
        source_path: 결과 JSON 절대 또는 base_path 기준 상대경로
        plan_name: plan 이름 (target 디렉토리 결정)
        base_path: project root
        label: baseline 라벨 (예: "v1_2"). None이면 source 파일명 사용.

    Returns:
        복사된 baseline 파일 절대경로.

    Raises:
        FileNotFoundError: source가 없을 때.
    """
    import shutil

    if not os.path.isabs(source_path):
        source_path = os.path.join(base_path, source_path)

    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source not found: {source_path}")

    if label is None:
        label = os.path.basename(source_path)
        if not label.endswith(".json"):
            label += ".json"
    elif not label.endswith(".json"):
        label = label + ".json"

    target_dir = os.path.join(base_path, "reports", plan_name, "baselines")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, label)

    shutil.copy2(source_path, target_path)
    return target_path
