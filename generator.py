"""
generator.py — schema.yaml 기반 테스트 케이스 자동 생성

profiles/schema.yaml에 정의된 설정 축과 기대값 규칙을 읽어
조합별 YAML 테스트 케이스를 profiles/generated/에 생성한다.
"""
from __future__ import annotations

import itertools
import os
from fnmatch import fnmatch

import yaml


def load_schema(schema_path: str) -> dict:
    with open(schema_path) as f:
        return yaml.safe_load(f)


def list_manual_cases(cases_dir: str) -> list[dict]:
    """profiles/cases/에서 수동 케이스의 edgeconf_changes를 수집한다."""
    manual = []
    if not os.path.isdir(cases_dir):
        return manual
    for fname in os.listdir(cases_dir):
        if not fname.endswith(".yaml"):
            continue
        path = os.path.join(cases_dir, fname)
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        setup = data.get("setup", {})
        changes = setup.get("edgeconf_changes", {})
        if changes:
            manual.append(changes)
    return manual


def _changes_match(a: dict, b: dict) -> bool:
    """두 edgeconf_changes dict가 동일한 설정 조합인지 비교한다."""
    return a == b


def generate_combinations(schema: dict):
    """교차 축의 모든 조합을 생성한다."""
    source = schema["sources"]["edgeconf"]
    axes = source["axes"]
    cross_axes = schema["generation"]["cross"]

    axis_combos = []
    for axis_name in cross_axes:
        axis_combos.append([
            (axis_name, combo) for combo in axes[axis_name]["combinations"]
        ])

    for combo in itertools.product(*axis_combos):
        yield combo


def resolve_rule(rules: dict, combo_key: str):
    """combo_key에 매칭되는 규칙을 찾는다.

    우선순위: 정확한 키 매칭 > 와일드카드 매칭 (fnmatch 스타일)
    와일드카드 충돌 시 가장 구체적인 패턴 우선 (* 개수가 적은 것)
    """
    if combo_key in rules:
        return rules[combo_key]
    matches = [(p, v) for p, v in rules.items() if fnmatch(combo_key, p)]
    if matches:
        matches.sort(key=lambda x: x[0].count("*"))
        return matches[0][1]
    return None


def build_case(combo: tuple, schema: dict) -> tuple[dict, str]:
    """조합 하나로부터 YAML 케이스 dict를 생성한다."""
    edgeconf_changes = {}
    combo_key_parts = []
    expects = {}

    for _axis_name, axis_combo in combo:
        edgeconf_changes.update(axis_combo["values"])
        combo_key_parts.append(axis_combo["name"])
        if "expect" in axis_combo:
            expects.update(axis_combo["expect"])

    combo_key = "+".join(combo_key_parts)
    name_parts = [c["name"] for _, c in combo]

    expectations = schema.get("expectations", {})
    checks: dict = {}

    # CPU range
    cpu_rules = expectations.get("cpu", {}).get("gst_range", {})
    gst_range = resolve_rule(cpu_rules, combo_key)
    if gst_range:
        checks["cpu"] = {"gst_range": gst_range}

    # Thermal
    thermal_rules = expectations.get("thermal", {}).get("warn_temp_c", {})
    warn_temp = resolve_rule(thermal_rules, combo_key)
    if warn_temp:
        checks["thermal"] = {"warn_temp_c": warn_temp}

    # cam_state (공통)
    cam_state_cfg = expectations.get("cam_state")
    if cam_state_cfg:
        checks["cam_state"] = {
            "dir": cam_state_cfg["dir"],
            "expected_state": cam_state_cfg["expected_state"],
            "max_streak": cam_state_cfg["max_streak"],
        }

    # Recording (채널 수에서 자동 계산)
    ch_count = expects.get("channel_count")
    if ch_count:
        checks["recording"] = {
            "expected_channels": ch_count,
            "session_progress": f"{ch_count}/{ch_count}",
        }

    # Stabilize
    stab_rules = expectations.get("stabilize_sec", {})
    stabilize = resolve_rule(stab_rules, combo_key) or 30

    case = {
        "name": f"[auto] {'_'.join(name_parts)}",
        "description": f"Auto-generated: {' '.join(name_parts)}",
        "setup": {
            "edgeconf_changes": edgeconf_changes,
            "reboot_after": True,
            "stabilize_sec": stabilize,
        },
        "checks": checks,
    }
    return case, "_".join(name_parts)


def generate_cases(profiles_dir: str) -> list[str]:
    """스키마를 읽어 케이스를 생성하고, 생성된 파일 경로 목록을 반환한다."""
    schema_path = os.path.join(profiles_dir, "schema.yaml")
    schema = load_schema(schema_path)

    cases_dir = os.path.join(profiles_dir, "cases")
    manual_changes = list_manual_cases(cases_dir)

    output_dir = os.path.join(
        os.path.dirname(os.path.abspath(profiles_dir)),
        schema["generation"]["output_dir"],
    )
    os.makedirs(output_dir, exist_ok=True)

    pattern = schema["generation"]["filename_pattern"]
    generated = []

    for combo in generate_combinations(schema):
        case_data, slug = build_case(combo, schema)

        # 수동 케이스와 중복 체크
        if any(_changes_match(case_data["setup"]["edgeconf_changes"], m)
               for m in manual_changes):
            combo_names = {c["name"]: axis for axis, c in combo}
            print(f"  skip: {slug} (manual case exists)")
            continue

        # 파일명 생성
        name_map = {axis: c["name"] for axis, c in combo}
        filename = pattern.format(**name_map)
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w") as f:
            f.write(f"# Auto-generated by pim-check generator\n")
            yaml.dump(case_data, f, default_flow_style=False, allow_unicode=True,
                      sort_keys=False)

        generated.append(filepath)
        print(f"  generated: {filename}")

    return generated
