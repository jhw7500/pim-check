#!/usr/bin/env python3
"""pim-check Inference Agent — 테스트 케이스 검증/갭분석/생성/추론.

Usage:
    python infer_agent.py --validate              # Level 1: 기존 케이스 기대값 검증
    python infer_agent.py --gap                    # Level 2: 누락 케이스 갭 분석
    python infer_agent.py --generate               # Level 3: 누락 케이스 자동 생성
    python infer_agent.py --infer                  # Level 4: 타겟 측정 기반 기대값 추론
    python infer_agent.py --all                    # 전체 파이프라인
    python infer_agent.py --validate --no-target   # 타겟 없이 정적 검증만
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import load_profile
from generator import (generate_cases, generate_combinations,
                       load_schema, resolve_rule)
from ssh import SshClient


PROFILES_DIR = str(BASE_DIR / "profiles")
SCHEMA_PATH = str(BASE_DIR / "profiles" / "schema.yaml")
CASES_DIR = str(BASE_DIR / "profiles" / "cases")
GENERATED_DIR = str(BASE_DIR / "profiles" / "generated")


class Finding:
    """검증 결과 항목."""
    def __init__(self, level: str, severity: str, case: str,
                 message: str, expected=None, actual=None):
        self.level = level          # validate, gap, generate, infer
        self.severity = severity    # error, warning, info
        self.case = case
        self.message = message
        self.expected = expected
        self.actual = actual

    def to_dict(self) -> dict:
        d = {"level": self.level, "severity": self.severity,
             "case": self.case, "message": self.message}
        if self.expected is not None:
            d["expected"] = self.expected
        if self.actual is not None:
            d["actual"] = self.actual
        return d


# ── Level 1: Validate ─────────────────────────────────────────

def validate_cases(ssh=None) -> list[Finding]:
    """기존 케이스 YAML의 기대값을 schema 규칙 및 타겟 실측과 비교."""
    findings = []
    schema = load_schema(SCHEMA_PATH)
    expectations = schema.get("expectations", {})

    # 모든 케이스 로드
    cases_path = Path(CASES_DIR)
    for yaml_file in sorted(cases_path.glob("*.yaml")):
        case_name = yaml_file.stem
        profile = load_profile(PROFILES_DIR, case=case_name)
        checks = profile.get("checks", {})
        setup = profile.get("setup", {})
        changes = setup.get("edgeconf_changes", {})

        # 설정 변경에서 조합 키 추출
        combo_key = _extract_combo_key(changes, schema)

        if not combo_key:
            continue  # 설정 변경 없는 케이스는 검증 스킵

        # CPU gst_range 검증
        cpu_config = checks.get("cpu", {})
        if cpu_config.get("gst_range"):
            gst_rules = expectations.get("cpu", {}).get("gst_range", {})
            expected_range = resolve_rule(gst_rules, combo_key)
            if expected_range and cpu_config["gst_range"] != expected_range:
                findings.append(Finding(
                    "validate", "warning", case_name,
                    "gst_range 불일치: schema 규칙 vs 케이스 정의",
                    expected=expected_range, actual=cpu_config["gst_range"],
                ))

        # stabilize_sec 검증
        stabilize = setup.get("stabilize_sec")
        if stabilize is not None:
            stab_rules = expectations.get("stabilize_sec", {})
            expected_stab = resolve_rule(stab_rules, combo_key)
            if expected_stab is not None and stabilize != expected_stab:
                findings.append(Finding(
                    "validate", "warning", case_name,
                    "stabilize_sec 불일치",
                    expected=expected_stab, actual=stabilize,
                ))

        # recording expected_channels 검증
        rec_config = checks.get("recording", {})
        expected_ch = rec_config.get("expected_channels")
        if expected_ch is not None:
            # channels 축에서 channel_count 추출
            ch_count = _infer_channel_count(changes, schema)
            if ch_count is not None and expected_ch != ch_count:
                findings.append(Finding(
                    "validate", "error", case_name,
                    "expected_channels 불일치",
                    expected=ch_count, actual=expected_ch,
                ))

    # 타겟 연결 시 실측 검증
    if ssh:
        findings.extend(_validate_against_target(ssh))

    if not findings:
        findings.append(Finding("validate", "info", "*",
                                "모든 케이스 기대값이 schema 규칙과 일치"))

    return findings


def _validate_against_target(ssh) -> list[Finding]:
    """타겟의 현재 상태를 측정하여 base.yaml 기본값과 비교."""
    findings = []

    # 현재 온도
    temp_out = ssh.run("cat /sys/devices/virtual/thermal/thermal_zone0/temp")
    if temp_out:
        try:
            temp_c = int(temp_out.strip()) / 1000
            profile = load_profile(PROFILES_DIR)
            warn_temp = profile.get("checks", {}).get("thermal", {}).get("warn_temp_c", 88)
            max_temp = profile.get("checks", {}).get("thermal", {}).get("max_temp_c", 93)
            if temp_c > max_temp:
                findings.append(Finding("validate", "error", "base",
                                        f"현재 온도 {temp_c}°C > max {max_temp}°C"))
            elif temp_c > warn_temp:
                findings.append(Finding("validate", "warning", "base",
                                        f"현재 온도 {temp_c}°C > warn {warn_temp}°C"))
        except ValueError:
            pass

    # 현재 edgeconf 읽기
    edgeconf_out = ssh.run("cat /root/shared_v/edgeconf_pim.json")
    if edgeconf_out:
        try:
            edgeconf = json.loads(edgeconf_out)
            # 현재 설정에 매칭되는 케이스가 있는지 확인
            current_combo = _edgeconf_to_combo_key(edgeconf, load_schema(SCHEMA_PATH))
            if current_combo:
                findings.append(Finding("validate", "info", "target",
                                        f"현재 타겟 설정: {current_combo}"))
        except json.JSONDecodeError:
            findings.append(Finding("validate", "warning", "target",
                                    "edgeconf JSON 파싱 실패"))

    return findings


def _extract_combo_key(changes: dict, schema: dict) -> str | None:
    """edgeconf_changes에서 조합 키 추출 (예: '720p+2ch+15fps')."""
    if not changes:
        return None

    parts = []
    for source_info in schema.get("sources", {}).values():
        for axis_name, axis_info in source_info.get("axes", {}).items():
            for combo in axis_info.get("combinations", []):
                combo_values = combo.get("values", {})
                if all(changes.get(k) == v for k, v in combo_values.items()
                       if k in changes):
                    if any(k in changes for k in combo_values):
                        parts.append(combo["name"])
                        break

    return "+".join(parts) if parts else None


def _infer_channel_count(changes: dict, schema: dict) -> int | None:
    """edgeconf_changes에서 채널 수 추론."""
    sources = schema.get("sources", {})
    edgeconf = sources.get("edgeconf", {})
    channels_axis = edgeconf.get("axes", {}).get("channels", {})

    for combo in channels_axis.get("combinations", []):
        combo_values = combo.get("values", {})
        if all(changes.get(k) == v for k, v in combo_values.items() if k in changes):
            return combo.get("expect", {}).get("channel_count")
    return None


def _edgeconf_to_combo_key(edgeconf: dict, schema: dict) -> str | None:
    """실제 edgeconf JSON에서 현재 조합 키 추출."""
    parts = []
    sources = schema.get("sources", {})
    edgeconf_source = sources.get("edgeconf", {})

    for axis_name, axis_info in edgeconf_source.get("axes", {}).items():
        for combo in axis_info.get("combinations", []):
            combo_values = combo.get("values", {})
            match = True
            for jq_key, expected_val in combo_values.items():
                # jq 키를 딕셔너리 경로로 변환 (.VHL_CAM.cam_width -> VHL_CAM.cam_width)
                actual = _resolve_jq_path(edgeconf, jq_key)
                if actual != expected_val:
                    match = False
                    break
            if match:
                parts.append(combo["name"])
                break

    return "+".join(parts) if parts else None


def _resolve_jq_path(data: dict, jq_path: str):
    """jq 경로를 딕셔너리에서 해석 (예: '.VHL_CAM.cam_width')."""
    keys = [k for k in jq_path.split(".") if k]
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


# ── Level 2: Gap Analysis ─────────────────────────────────────

def gap_analysis() -> list[Finding]:
    """schema 정의 조합 vs 실제 존재하는 케이스 비교."""
    findings = []
    schema = load_schema(SCHEMA_PATH)
    generation = schema.get("generation", {})
    groups = generation.get("groups", [])

    existing_manual = {f.stem for f in Path(CASES_DIR).glob("*.yaml")}
    gen_path = Path(GENERATED_DIR)
    existing_generated = {f.stem for f in gen_path.glob("*.yaml")} if gen_path.exists() else set()

    all_existing = existing_manual | existing_generated

    total_expected = 0
    total_found = 0

    for group in groups:
        source_name = group["source"]
        cross_axes = group["cross"]
        source = schema["sources"].get(source_name, {})
        axes = source.get("axes", {})

        # 교차 조합 생성
        combos = list(generate_combinations(axes, cross_axes))
        pattern = group.get("filename_pattern", "")

        for combo in combos:
            total_expected += 1
            # 파일명 생성
            name_parts = {cross_axes[i]: combo[i][1]["name"] for i in range(len(cross_axes))}
            filename = pattern.format(**name_parts).replace(".yaml", "")

            if filename in all_existing:
                total_found += 1
            else:
                # 수동 케이스 중 동일 설정이 있는지 확인
                manual_match = _find_manual_match(combo, cross_axes, axes, existing_manual)
                if manual_match:
                    total_found += 1
                    findings.append(Finding("gap", "info", filename,
                                            f"수동 케이스 '{manual_match}'로 커버됨"))
                else:
                    combo_desc = "+".join(c[1]["name"] for c in combo)
                    findings.append(Finding("gap", "warning", filename,
                                            f"누락: {group['name']} — {combo_desc}"))

    findings.insert(0, Finding("gap", "info", "*",
                                f"커버리지: {total_found}/{total_expected} "
                                f"({total_found*100//max(total_expected,1)}%)"))

    return findings


def _find_manual_match(combo, cross_axes, axes, manual_cases) -> str | None:
    """조합과 동일한 설정을 가진 수동 케이스 찾기."""
    # 조합의 edgeconf_changes 구성
    target_changes = {}
    for i, axis_name in enumerate(cross_axes):
        combo_name = combo[i][0]
        axis_info = axes.get(axis_name, {})
        for c in axis_info.get("combinations", []):
            if c["name"] == combo_name:
                target_changes.update(c.get("values", {}))

    if not target_changes:
        return None

    for case_name in manual_cases:
        try:
            profile = load_profile(PROFILES_DIR, case=case_name)
            changes = profile.get("setup", {}).get("edgeconf_changes", {})
            if changes and all(changes.get(k) == v for k, v in target_changes.items()):
                return case_name
        except Exception:
            continue
    return None


# ── Level 3: Generate ─────────────────────────────────────────

def generate_missing(dry_run: bool = False) -> list[Finding]:
    """누락된 케이스를 자동 생성 (generator.py 래핑)."""
    findings = []

    if dry_run:
        # 갭 분석 결과에서 누락 항목만 추출
        gaps = gap_analysis()
        missing = [f for f in gaps if f.severity == "warning" and f.level == "gap"]
        findings.append(Finding("generate", "info", "*",
                                f"생성 대상: {len(missing)}건 (dry-run)"))
        for m in missing:
            findings.append(Finding("generate", "info", m.case,
                                    f"생성 예정: {m.message}"))
        return findings

    # 실제 생성
    try:
        generated = generate_cases(PROFILES_DIR)
        findings.append(Finding("generate", "info", "*",
                                f"생성 완료: {len(generated)}건"))
        for path in generated:
            findings.append(Finding("generate", "info", Path(path).stem,
                                    f"생성됨: {path}"))
    except Exception as e:
        findings.append(Finding("generate", "error", "*",
                                f"생성 실패: {e}"))

    return findings


# ── Level 4: Infer ─────────────────────────────────────────────

def infer_expectations(ssh) -> list[Finding]:
    """타겟 실측 데이터 기반 기대값 추론."""
    findings = []

    if not ssh:
        findings.append(Finding("infer", "error", "*",
                                "타겟 연결 필요 (--no-target 제거)"))
        return findings

    schema = load_schema(SCHEMA_PATH)
    expectations = schema.get("expectations", {})

    # 현재 상태 측정
    measurements = _measure_current_state(ssh)
    if not measurements:
        findings.append(Finding("infer", "error", "*", "타겟 측정 실패"))
        return findings

    findings.append(Finding("infer", "info", "target",
                            f"현재 측정: {json.dumps(measurements, ensure_ascii=False)}"))

    # 현재 설정의 조합 키
    edgeconf_out = ssh.run("cat /root/shared_v/edgeconf_pim.json")
    if not edgeconf_out:
        findings.append(Finding("infer", "error", "*", "edgeconf 읽기 실패"))
        return findings

    edgeconf = json.loads(edgeconf_out)
    combo_key = _edgeconf_to_combo_key(edgeconf, schema)

    if combo_key:
        findings.append(Finding("infer", "info", "target",
                                f"현재 조합: {combo_key}"))

        # gst_range 추론: 현재 CPU를 기반으로 ±마진 계산
        gst_cpu = measurements.get("gst_cpu_pct")
        if gst_cpu is not None:
            margin = max(10, gst_cpu * 0.3)  # 30% 마진, 최소 10
            inferred_range = [max(0, int(gst_cpu - margin)),
                              min(100, int(gst_cpu + margin))]

            # schema 규칙과 비교
            gst_rules = expectations.get("cpu", {}).get("gst_range", {})
            schema_range = resolve_rule(gst_rules, combo_key)

            if schema_range:
                if (gst_cpu < schema_range[0] or gst_cpu > schema_range[1]):
                    findings.append(Finding(
                        "infer", "warning", combo_key,
                        f"실측 CPU {gst_cpu}%가 schema 범위 밖",
                        expected=schema_range, actual=gst_cpu,
                    ))
                else:
                    findings.append(Finding(
                        "infer", "info", combo_key,
                        f"실측 CPU {gst_cpu}%가 schema 범위 내 {schema_range}",
                    ))

                # 추론된 범위 vs schema 범위 비교
                if inferred_range != schema_range:
                    findings.append(Finding(
                        "infer", "info", combo_key,
                        f"추론 범위 {inferred_range} vs schema {schema_range}",
                        expected=schema_range, actual=inferred_range,
                    ))
            else:
                findings.append(Finding(
                    "infer", "warning", combo_key,
                    f"schema에 규칙 없음. 추론 범위: {inferred_range}",
                    actual=inferred_range,
                ))

        # thermal 추론
        temp_c = measurements.get("temp_c")
        if temp_c is not None:
            findings.append(Finding(
                "infer", "info", combo_key,
                f"현재 온도 {temp_c}°C — "
                f"warn 권장: {int(temp_c) + 5}°C, max 권장: {int(temp_c) + 10}°C",
            ))

    return findings


def _measure_current_state(ssh) -> dict | None:
    """타겟의 현재 CPU, 온도, 프로세스 상태 측정."""
    measurements = {}

    # gstApp CPU
    cpu_out = ssh.run("ps -C gstApp -o %cpu= 2>/dev/null | head -1")
    if cpu_out and cpu_out.strip():
        try:
            measurements["gst_cpu_pct"] = float(cpu_out.strip())
        except ValueError:
            pass

    # 온도
    temp_out = ssh.run("cat /sys/devices/virtual/thermal/thermal_zone0/temp")
    if temp_out and temp_out.strip():
        try:
            measurements["temp_c"] = int(temp_out.strip()) / 1000
        except ValueError:
            pass

    # 프로세스 목록
    ps_out = ssh.run("ps -eo comm= 2>/dev/null")
    if ps_out:
        procs = [p.strip() for p in ps_out.splitlines() if p.strip()]
        measurements["running_procs"] = procs

    # cam_state
    state_out = ssh.run("cat /tmp/cam_state/state 2>/dev/null")
    if state_out and state_out.strip():
        measurements["cam_state"] = state_out.strip()

    return measurements if measurements else None


# ── Report ─────────────────────────────────────────────────────

def print_report(all_findings: list[Finding], as_json: bool = False):
    """결과를 터미널 또는 JSON으로 출력."""
    if as_json:
        print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "total": len(all_findings),
            "errors": sum(1 for f in all_findings if f.severity == "error"),
            "warnings": sum(1 for f in all_findings if f.severity == "warning"),
            "findings": [f.to_dict() for f in all_findings],
        }, ensure_ascii=False, indent=2))
        return

    print(f"\n{'=' * 60}")
    print(f"  pim-check Inference Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}\n")

    current_level = None
    for f in all_findings:
        if f.level != current_level:
            current_level = f.level
            label = {"validate": "VALIDATE (기대값 검증)",
                     "gap": "GAP (누락 분석)",
                     "generate": "GENERATE (케이스 생성)",
                     "infer": "INFER (기대값 추론)"}.get(f.level, f.level)
            print(f"  [{label}]")

        icon = {"error": "[X]", "warning": "[!]", "info": "[+]"}[f.severity]
        msg = f"    {icon} [{f.case}] {f.message}"
        if f.expected is not None:
            msg += f"  (expected={f.expected}"
            if f.actual is not None:
                msg += f", actual={f.actual}"
            msg += ")"
        elif f.actual is not None:
            msg += f"  (actual={f.actual})"
        print(msg)

    errors = sum(1 for f in all_findings if f.severity == "error")
    warnings = sum(1 for f in all_findings if f.severity == "warning")

    print(f"\n{'=' * 60}")
    print(f"  Findings: {len(all_findings)} total, {errors} errors, {warnings} warnings")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="pim-check Inference Agent")
    parser.add_argument("--validate", action="store_true", help="Level 1: 기대값 검증")
    parser.add_argument("--gap", action="store_true", help="Level 2: 갭 분석")
    parser.add_argument("--generate", action="store_true", help="Level 3: 케이스 생성")
    parser.add_argument("--infer", action="store_true", help="Level 4: 기대값 추론")
    parser.add_argument("--all", action="store_true", help="전체 파이프라인")
    parser.add_argument("--no-target", action="store_true", help="타겟 없이 정적 분석만")
    parser.add_argument("--dry-run", action="store_true", help="생성 시 파일 미작성")
    parser.add_argument("--host", default=os.environ.get("TARGET_HOST", "192.168.0.5"),
                        help="타겟 호스트 (env: TARGET_HOST)")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="root")
    parser.add_argument("--json", dest="json_output", action="store_true")
    args = parser.parse_args()

    # 아무 옵션 없으면 --all
    if not any([args.validate, args.gap, args.generate, args.infer, args.all]):
        args.all = True

    ssh = None
    if not args.no_target:
        ssh = SshClient(args.host, args.user, args.password)
        if not ssh.check_connectivity():
            print(f"ERROR: Cannot connect to {args.host}")
            sys.exit(1)

    all_findings: list[Finding] = []

    if args.validate or args.all:
        all_findings.extend(validate_cases(ssh))

    if args.gap or args.all:
        all_findings.extend(gap_analysis())

    if args.generate or args.all:
        all_findings.extend(generate_missing(dry_run=args.dry_run or args.all))

    if args.infer or args.all:
        all_findings.extend(infer_expectations(ssh))

    print_report(all_findings, as_json=args.json_output)

    errors = sum(1 for f in all_findings if f.severity == "error")
    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()
