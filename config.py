"""
config.py - YAML 설정 로더 (base + case override 패턴)
"""
from __future__ import annotations

import copy
import os
import yaml


def deep_merge(base: dict, override: dict) -> dict:
    """override를 base에 딥 머지한 새로운 dict를 반환한다. base는 변경되지 않는다.

    - 두 값이 모두 dict이면 재귀 머지
    - 그 외에는 override 값이 우선
    """
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


# `teardown:` 섹션에서 실제로 읽히는 키. 여기 없는 키는 조용히 무시되므로 경고한다 —
# recovery_command 를 teardown 에 두면 안 읽히던 버그(pim-check#75)가 정확히 그
# "엉뚱한 섹션에 뒀는데 아무도 말해주지 않는" 형태였다.
TEARDOWN_KEYS = frozenset({"recovery_command"})


def _warn_unknown_teardown_keys(profile: dict, case: str | None) -> None:
    teardown = profile.get("teardown")
    if not isinstance(teardown, dict):
        return
    unknown = sorted(set(teardown) - TEARDOWN_KEYS)
    if unknown:
        print(f"WARNING: {case or 'base'}: teardown 아래 {unknown} 는 읽히지 않는다 "
              f"(지원: {sorted(TEARDOWN_KEYS)}) — setup: 섹션으로 옮길 것")


def load_profile(profiles_dir: str, case: str | None = None) -> dict:
    """base.yaml을 로드하고, case가 지정되면 cases/{case}.yaml을 딥 머지하여 반환한다.

    Args:
        profiles_dir: base.yaml과 cases/ 디렉토리가 있는 경로
        case: 케이스 파일명 (확장자 제외). None이면 base만 반환.

    Returns:
        머지된 프로파일 dict

    Raises:
        FileNotFoundError: case 파일이 존재하지 않을 때
    """
    base_path = os.path.join(profiles_dir, "base.yaml")
    with open(base_path, "r") as f:
        profile = yaml.safe_load(f)

    if case is not None:
        case_path = os.path.join(profiles_dir, "cases", f"{case}.yaml")
        if not os.path.exists(case_path):
            # generated/ 디렉토리에서도 탐색
            gen_path = os.path.join(profiles_dir, "generated", f"{case}.yaml")
            if os.path.exists(gen_path):
                case_path = gen_path
            else:
                raise FileNotFoundError(f"Case file not found: {case_path}")
        with open(case_path, "r") as f:
            case_data = yaml.safe_load(f) or {}
        profile = deep_merge(profile, case_data)

    _warn_unknown_teardown_keys(profile, case)
    return profile
