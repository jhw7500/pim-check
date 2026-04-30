"""
plan.py — Declarative Release Plan loader, linter, and case resolver.

v1 scope: load_plan + lint_plan + resolve_cases + Plan/GateResult dataclasses.
v1.1+ adds: execute_plan, evaluate_gate, render_reports, tag selector, case_overrides.

Design doc: ~/.gstack/projects/jhw7500-pim-check/jhw-main-design-20260430-130751.md
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Any

import yaml


# ── 데이터 모델 ────────────────────────────────────────────────

DEFAULT_EXECUTION: dict[str, Any] = {
    "stop_on_fail": False,
    "case_retry": 0,
    "retry_wait_sec": 0,
    "reboot_wait_sec": 300,
    "wait_between_cases": 0,
}

DEFAULT_GATE: dict[str, Any] = {
    "threshold_pass_rate": 1.0,
    "allow_known_issue": True,
}

ALLOWED_REPORT_FORMATS = {"json", "html", "junit"}
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
            for key in ("stop_on_fail",):
                if key in exec_cfg and not isinstance(exec_cfg[key], bool):
                    errors.append(f"execution.{key}은 bool이어야 합니다.")
            for key in ("case_retry", "retry_wait_sec",
                        "reboot_wait_sec", "wait_between_cases"):
                if key in exec_cfg and not isinstance(exec_cfg[key], int):
                    errors.append(f"execution.{key}은 정수여야 합니다.")
                elif key in exec_cfg and exec_cfg[key] < 0:
                    errors.append(f"execution.{key}은 0 이상이어야 합니다.")

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
