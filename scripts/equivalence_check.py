#!/usr/bin/env python3
"""
scripts/equivalence_check.py — run_*.py 결과 vs plan-driven 결과 동등성 비교.

design doc v1 success criteria의 마지막 항목: comprehensive.yaml 결과가
run_comprehensive_verify.py와 동등한가 검증. 이 도구는 두 결과 JSON을
normalize하여 case 단위 binary 비교 후 카테고리 분류 출력.

v1 scope (단순 binary 비교):
  - case_name 매핑 (--mapping FILE 옵션, 도메인별 다름)
  - 카테고리: MATCHED / MISMATCHED / LEFT_ONLY / RIGHT_ONLY
  - mismatch가 0이고 LEFT_ONLY/RIGHT_ONLY이 매핑되지 않은 case만이면 PASS

v1.1 scope (design doc 영속화):
  - per-check NUMERIC_DRIFT 분류 (CPU%, temp_c 같은 수치 ±tolerance)
  - per-check MISSING_LOGIC 분류 (한쪽에만 존재하는 check name)

지원하는 left (러너 결과 JSON) format:
  - run_comprehensive_verify: list of {name, result, ...}
  - run_smart_verify:         list of {name, result, ...}
  - run_mixed_combo_verify:   list of {test_id, name, pass, results, ...}
  - run_channel_verify:       list of {case, passed, ...}
  - run_bps_quick:            list of {channel, bps, result, ...}

right format (plan-driven, render_reports json output):
  - {plan, timestamp, host, executions: [{case_name, passed, ...}], gate}

사용법:
  python3 scripts/equivalence_check.py \\
    --left  comprehensive_results.json \\
    --right reports/comprehensive/20260430_120000.json \\
    [--mapping mapping.json]

mapping.json 예 (run case_name → plan case_name):
  {
    "p2_quad_720p_ch0_vflip": "multi_4ch_720p",
    "p2_quad_720p_ch1_vflip": "multi_4ch_720p",
    ...
  }

(여러 left case가 한 plan case에 매핑되는 경우, plan case의 결과로 모두 비교)

Exit code:
  0 = 동등 (mismatch 0)
  1 = 동등 안 함 (mismatch > 0)
  3 = 입력 파일 에러
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaseStatus:
    """단일 case의 binary 상태 + 출처 라벨."""
    name: str
    passed: bool
    source_label: str = ""   # 디버그용 (예: "result=PASS" 또는 "passed=true")


def _load_json(path: str) -> Any:
    with open(path, "r") as f:
        return json.load(f)


def normalize_left(data: Any) -> list[CaseStatus]:
    """run_*.py 결과 JSON을 [CaseStatus] 리스트로 normalize.

    여러 형식 지원 — 'result' 필드 기준이거나 'passed' 또는 'pass' 키.
    """
    if not isinstance(data, list):
        raise ValueError(f"left JSON은 list여야 합니다 (run_*.py 형식). 받은 타입: {type(data).__name__}")

    out: list[CaseStatus] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        # case name 추출 (여러 키 지원)
        name = (entry.get("name")
                or entry.get("case")
                or entry.get("case_name")
                or "")
        if not name:
            continue

        # passed 추출
        if "result" in entry:
            # 'PASS' / 'FAIL' / 'NO_SSH' / ... 같은 string
            result = str(entry["result"]).upper()
            passed = result == "PASS"
            label = f"result={result}"
        elif "passed" in entry:
            v = entry["passed"]
            if isinstance(v, bool):
                passed = v
            else:
                # 'PASS' / 'FAIL' string
                passed = str(v).upper() == "PASS"
            label = f"passed={v}"
        elif "pass" in entry:
            v = entry["pass"]
            passed = bool(v)
            label = f"pass={v}"
        else:
            continue   # 인식 못 하면 skip

        out.append(CaseStatus(name=str(name), passed=passed, source_label=label))
    return out


def normalize_right(data: Any) -> list[CaseStatus]:
    """plan-driven 결과 JSON ({executions: [...]})을 [CaseStatus]로 normalize."""
    if not isinstance(data, dict):
        raise ValueError(f"right JSON은 dict여야 합니다 (plan render_reports 형식). 받은 타입: {type(data).__name__}")
    execs = data.get("executions")
    if not isinstance(execs, list):
        raise ValueError("right JSON에 executions 리스트 없음 (plan 형식 아님)")

    out: list[CaseStatus] = []
    for exe in execs:
        if not isinstance(exe, dict):
            continue
        name = exe.get("case_name", "")
        if not name:
            continue
        passed = bool(exe.get("passed", False))
        out.append(CaseStatus(name=str(name), passed=passed, source_label=f"passed={passed}"))
    return out


def _apply_mapping(name: str, mapping: dict[str, str]) -> str:
    """case_name에 mapping 적용. 없으면 원본 그대로."""
    return mapping.get(name, name)


@dataclass
class EquivalenceReport:
    matched: list[tuple[str, str]] = field(default_factory=list)        # (left_name, right_name)
    mismatched: list[tuple[str, str, bool, bool]] = field(default_factory=list)  # (left, right, left_passed, right_passed)
    left_only: list[str] = field(default_factory=list)
    right_only: list[str] = field(default_factory=list)


def compare(left: list[CaseStatus],
            right: list[CaseStatus],
            mapping: dict[str, str] | None = None) -> EquivalenceReport:
    """left와 right의 case 단위 binary 비교.

    mapping 적용 후 left.name → right.name 일치 여부 + passed 동등 비교.
    """
    mapping = mapping or {}
    report = EquivalenceReport()

    # right를 name -> CaseStatus로 인덱싱 (빠른 lookup)
    right_by_name: dict[str, CaseStatus] = {}
    for r in right:
        right_by_name[r.name] = r

    seen_right: set[str] = set()

    for ls in left:
        mapped_name = _apply_mapping(ls.name, mapping)
        r = right_by_name.get(mapped_name)
        if r is None:
            report.left_only.append(ls.name)
            continue
        seen_right.add(mapped_name)
        if ls.passed == r.passed:
            report.matched.append((ls.name, r.name))
        else:
            report.mismatched.append((ls.name, r.name, ls.passed, r.passed))

    # right에 있는데 left에 없는 case
    for r in right:
        if r.name not in seen_right:
            report.right_only.append(r.name)

    return report


def print_report(report: EquivalenceReport,
                 left_total: int, right_total: int) -> None:
    matched = len(report.matched)
    mismatched = len(report.mismatched)
    lo = len(report.left_only)
    ro = len(report.right_only)

    print("=== Equivalence Report ===")
    print(f"  Left:  {left_total} cases")
    print(f"  Right: {right_total} cases")
    print("")
    print(f"  MATCHED:    {matched}")
    print(f"  MISMATCHED: {mismatched}")
    print(f"  LEFT_ONLY:  {lo} (mapping으로 매칭 안 된 left case)")
    print(f"  RIGHT_ONLY: {ro} (mapping으로 매칭 안 된 right case)")
    print("")

    if mismatched > 0:
        print("Mismatched cases (left passed != right passed):")
        for ln, rn, lp, rp in report.mismatched[:20]:
            print(f"  {ln!r} -> {rn!r}: left={lp}, right={rp}")
        if mismatched > 20:
            print(f"  ... +{mismatched - 20} more")
        print()

    if lo > 0 and lo <= 20:
        print(f"Left-only ({lo}):")
        for n in report.left_only[:20]:
            print(f"  {n}")
        print()
    elif lo > 20:
        print(f"Left-only ({lo}, first 20):")
        for n in report.left_only[:20]:
            print(f"  {n}")
        print(f"  ... +{lo - 20} more\n")

    if ro > 0 and ro <= 20:
        print(f"Right-only ({ro}):")
        for n in report.right_only[:20]:
            print(f"  {n}")
        print()
    elif ro > 20:
        print(f"Right-only ({ro}, first 20):")
        for n in report.right_only[:20]:
            print(f"  {n}")
        print(f"  ... +{ro - 20} more\n")

    verdict = "EQUIVALENT" if mismatched == 0 else "DIVERGED"
    print(f"=== VERDICT: {verdict} ===")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="run_*.py 결과 vs plan-driven 결과 동등성 비교 (v1 binary)."
    )
    parser.add_argument("--left", required=True, help="run_*.py 결과 JSON 경로")
    parser.add_argument("--right", required=True, help="plan-driven 결과 JSON 경로")
    parser.add_argument("--mapping", default=None,
                        help="case_name 매핑 JSON 파일 (left → right). 형식: {\"left_name\": \"right_name\"}")
    args = parser.parse_args(argv)

    try:
        left_raw = _load_json(args.left)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: --left 로드 실패: {e}")
        return 3

    try:
        right_raw = _load_json(args.right)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: --right 로드 실패: {e}")
        return 3

    try:
        left = normalize_left(left_raw)
    except ValueError as e:
        print(f"ERROR: --left normalize 실패: {e}")
        return 3

    try:
        right = normalize_right(right_raw)
    except ValueError as e:
        print(f"ERROR: --right normalize 실패: {e}")
        return 3

    mapping: dict[str, str] | None = None
    if args.mapping:
        try:
            mapping = _load_json(args.mapping)
            if not isinstance(mapping, dict):
                print("ERROR: --mapping은 {left: right} dict여야 합니다.")
                return 3
        except (OSError, json.JSONDecodeError) as e:
            print(f"ERROR: --mapping 로드 실패: {e}")
            return 3

    report = compare(left, right, mapping)
    print_report(report, len(left), len(right))

    return 0 if not report.mismatched else 1


if __name__ == "__main__":
    sys.exit(main())
