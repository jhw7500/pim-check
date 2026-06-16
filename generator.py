"""
generator.py — schema.yaml 기반 테스트 케이스 자동 생성

profiles/schema.yaml에 정의된 설정 축과 기대값 규칙을 읽어
조합별 YAML 테스트 케이스를 profiles/generated/에 생성한다.

groups 구조를 지원하여 edgeconf와 ord_vcm 등 여러 소스에서
독립적으로 케이스를 생성할 수 있다.
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


def generate_combinations(axes: dict, cross_axes: list[str]):
    """교차 축의 모든 조합을 생성한다."""
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


def build_case(combo: tuple, schema: dict, no_reboot: bool = False) -> tuple[dict, str]:
    """조합 하나로부터 YAML 케이스 dict를 생성한다."""
    # capture.enable 기본값 false 명시 — cap_on axis가 true로 override.
    # 미명시 시 직전 case의 capture=true가 잔존하여 record=false와 결합,
    # 녹화 파이프라인이 멈추는 버그 방지 (each case가 capture 상태를 완전 정의).
    edgeconf_changes = {".VHL_CAM.capture.enable": False}
    combo_key_parts = []
    expects = {}

    verify_commands = []
    for _axis_name, axis_combo in combo:
        edgeconf_changes.update(axis_combo["values"])
        combo_key_parts.append(axis_combo["name"])
        if "expect" in axis_combo:
            expects.update(axis_combo["expect"])
        if "verify" in axis_combo:
            verify_commands.extend(axis_combo["verify"])

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

    # Stabilize / reboot
    stab_rules = expectations.get("stabilize_sec", {})
    stabilize = resolve_rule(stab_rules, combo_key) or 30

    if no_reboot:
        case = {
            "name": f"[auto] {'_'.join(name_parts)}",
            "description": f"Auto-generated: {' '.join(name_parts)}",
            "checks": checks,
        }
        # no_reboot 케이스는 edgeconf_changes 대신 custom_commands로 검증
        case["monitor"] = {"duration_sec": 0}
        case["checks"]["custom_commands"] = _build_config_checks(edgeconf_changes)
        if verify_commands:
            case["checks"]["custom_commands"].extend(verify_commands)
    else:
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
        if verify_commands:
            if "custom_commands" not in case["checks"]:
                case["checks"]["custom_commands"] = []
            case["checks"]["custom_commands"].extend(verify_commands)

    return case, "_".join(name_parts)


def _build_config_checks(changes: dict) -> list[dict]:
    """edgeconf_changes를 custom_commands 검증 항목으로 변환한다."""
    commands = []
    for jq_path, expected in changes.items():
        # ord_vcm_conf.json 경로 판별
        if jq_path.startswith(".ORD") or jq_path.startswith(".VCM") or jq_path.startswith(".ETC"):
            conf_file = "/root/shared_v/ord_vcm_conf.json"
        else:
            conf_file = "/root/shared_v/edgeconf_pim.json"

        if isinstance(expected, bool):
            expected_str = "true" if expected else "false"
        else:
            expected_str = str(expected)

        commands.append({
            "name": f"config {jq_path} == {expected_str}",
            "command": f"jq '{jq_path}' {conf_file}",
            "expected": expected_str,
            "on_fail": f"{jq_path} 값이 {expected_str}와 다름",
        })
    return commands


def generate_cases(profiles_dir: str) -> list[str]:
    """스키마를 읽어 케이스를 생성하고, 생성된 파일 경로 목록을 반환한다."""
    schema_path = os.path.join(profiles_dir, "schema.yaml")
    schema = load_schema(schema_path)

    cases_dir = os.path.join(profiles_dir, "cases")
    manual_changes = list_manual_cases(cases_dir)

    generation = schema["generation"]
    sources = schema["sources"]
    generated = []
    # 녹화 연속성(recording.session_progress)은 보드 전역 동작이라 모든 조합에
    # 중복 검증할 필요가 없다. 채널수별 1개 대표 케이스에만 남기고(축소) 나머지는
    # 제거한다. expected_channels 는 infer_agent 가 쓰므로 유지.
    # 대표 선정은 schema 순회상 채널수별 first-seen — 스키마 축 순서를 바꾸면 어느
    # 케이스가 대표가 되는지도 바뀐다(검증 효과는 동일).
    seen_session_ch = set()

    # groups 구조 지원 (하위 호환: groups가 없으면 단일 그룹으로 동작)
    if "groups" in generation:
        groups = generation["groups"]
    else:
        groups = [{
            "name": "default",
            "source": "edgeconf",
            "cross": generation["cross"],
            "output_dir": generation["output_dir"],
            "filename_pattern": generation["filename_pattern"],
        }]

    for group in groups:
        source_name = group["source"]
        source = sources[source_name]
        axes = source["axes"]
        cross_axes = group["cross"]
        no_reboot = group.get("no_reboot", False)

        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(profiles_dir)),
            group["output_dir"],
        )
        os.makedirs(output_dir, exist_ok=True)

        pattern = group["filename_pattern"]
        print(f"\n[{group['name']}] source={source_name}, axes={cross_axes}")

        for combo in generate_combinations(axes, cross_axes):
            case_data, slug = build_case(combo, schema, no_reboot=no_reboot)

            # 수동 케이스와 중복 체크 (reboot 케이스만)
            if not no_reboot and "setup" in case_data:
                if any(_changes_match(case_data["setup"]["edgeconf_changes"], m)
                       for m in manual_changes):
                    print(f"  skip: {slug} (manual case exists)")
                    continue

            # session_progress 축소: 채널수별 첫 케이스만 유지, 나머지는 제거.
            rec = case_data.get("checks", {}).get("recording")
            if rec and rec.get("session_progress") is not None:
                chc = rec.get("expected_channels")
                if chc in seen_session_ch:
                    rec.pop("session_progress", None)
                else:
                    seen_session_ch.add(chc)

            # 파일명 생성
            name_map = {axis: c["name"] for axis, c in combo}
            filename = pattern.format(**name_map)
            filepath = os.path.join(output_dir, filename)

            with open(filepath, "w") as f:
                f.write("# Auto-generated by pim-check generator\n")
                yaml.dump(case_data, f, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)

            generated.append(filepath)
            print(f"  generated: {filename}")

    return generated
