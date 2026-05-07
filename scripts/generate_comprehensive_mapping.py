#!/usr/bin/env python3
"""
scripts/generate_comprehensive_mapping.py — run_comprehensive_verify.py scenario를
multi case로 자동 매핑하는 JSON 생성기.

run_comprehensive_verify.generate_scenarios()를 직접 import해서 96 scenario를
얻고, 각 scenario의 활성 채널 + 해상도로 8 mandatory combinations 중 어느
multi case에 해당하는지 결정.

매핑 가능한 케이스 (multi case에 정의된 조합):
  - p2_quad_*           → multi_4ch_{res}    (ch0/1/2/3)
  - p3_samebus_i2c2_*   → multi_2ch_01_{res} (ch0+ch1)

매핑 불가 (multi case에 없는 조합):
  - p3_samebus_i2c1_*   → ch2+ch3 (multi_2ch_23 없음)
  - p3_crossbus_lo_*    → ch0+ch2 (multi_2ch_02 없음)
  - p3_crossbus_hi_*    → ch1+ch3 (multi_2ch_13 없음)

매핑 불가 케이스는 unmapped 리스트로 stdout 출력 — equivalence_check에서
LEFT_ONLY로 분류됨. 이건 의도된 design 차이 (사용자가 8 mandatory combinations만
선정했으므로).

사용:
  python3 scripts/generate_comprehensive_mapping.py
    → profiles/plans/comprehensive_mapping.json 생성

  python3 scripts/equivalence_check.py \\
    --left comprehensive_results.json \\
    --right reports/comprehensive/{ts}.json \\
    --mapping profiles/plans/comprehensive_mapping.json

Exit code: 0 (항상). 실행 자체 실패는 import error로 raise.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


# multi case 정의 (8 mandatory combinations) — 활성 채널 frozenset → 이름 템플릿
MULTI_COMBOS: dict[frozenset, str] = {
    frozenset([0, 1, 2, 3]): "multi_4ch_{res}",
    frozenset([0, 1, 2]):    "multi_3ch_012_{res}",
    frozenset([1, 2, 3]):    "multi_3ch_123_{res}",
    frozenset([0, 1]):       "multi_2ch_01_{res}",
    frozenset([0, 3]):       "multi_2ch_03_{res}",
    frozenset([1, 2]):       "multi_2ch_12_{res}",
    frozenset([0]):          "multi_1ch_0_{res}",
    frozenset([3]):          "multi_1ch_3_{res}",
}


def scenario_active_channels(scen: dict) -> set[int]:
    """scenario의 changes 리스트에서 활성 채널(enable=True)만 추출."""
    active: set[int] = set()
    for path, value in scen.get("changes", []):
        if ".enable" not in path:
            continue
        if value is not True:
            continue
        for ch in (0, 1, 2, 3):
            if f".ch{ch}.enable" in path:
                active.add(ch)
                break
    return active


def build_mapping(scenarios: list) -> tuple[dict[str, str], list[dict]]:
    """{run_name: multi_name} 매핑 + unmapped 리스트 반환.

    Args:
        scenarios: run_comprehensive_verify.generate_scenarios() 결과
    Returns:
        (mapping, unmapped) — mapping은 매핑된 scenario, unmapped는 이유 포함 dict.
    """
    mapping: dict[str, str] = {}
    unmapped: list[dict] = []

    for scen in scenarios:
        name = scen.get("name")
        res = scen.get("res")
        if not name or not res:
            continue
        active = scenario_active_channels(scen)
        active_fset = frozenset(active)
        template = MULTI_COMBOS.get(active_fset)
        if template:
            mapping[name] = template.format(res=res)
        else:
            unmapped.append({
                "scenario": name,
                "active_channels": sorted(active),
                "res": res,
                "reason": "8 mandatory combinations에 해당 조합 없음",
            })

    return mapping, unmapped


def write_mapping(mapping: dict[str, str], out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    # run_comprehensive_verify.generate_scenarios()를 import + 호출.
    # 이 함수는 SSH 의존 없는 순수 데이터 생성 함수.
    try:
        from run_comprehensive_verify import generate_scenarios
    except ImportError as exc:
        print(f"ERROR: run_comprehensive_verify.py import 실패: {exc}")
        return 3

    scenarios = generate_scenarios()
    mapping, unmapped = build_mapping(scenarios)

    out_path = BASE / "profiles" / "plans" / "comprehensive_mapping.json"
    write_mapping(mapping, str(out_path))

    rel_out = os.path.relpath(out_path, BASE)
    print(f"Total scenarios: {len(scenarios)}")
    print(f"  mapped:   {len(mapping)} → {rel_out}")
    print(f"  unmapped: {len(unmapped)}")

    if unmapped:
        # 활성 채널 조합별 카운트
        combo_counts = Counter(tuple(u["active_channels"]) for u in unmapped)
        print()
        print("Unmapped scenarios (8 mandatory combinations에 없는 조합):")
        for combo, count in sorted(combo_counts.items()):
            chs = ", ".join(f"ch{c}" for c in combo)
            print(f"  {{{chs}}}: {count} scenarios")
        print()
        print("이는 design 차이 (사용자가 8 mandatory combinations만 선정).")
        print("equivalence_check에서 LEFT_ONLY로 분류됩니다.")

    # 분포 요약
    multi_counts = Counter(mapping.values())
    print()
    print(f"Multi case 분포 (mapped {len(mapping)}건):")
    for multi, count in sorted(multi_counts.items()):
        print(f"  {multi}: {count} scenarios")

    return 0


if __name__ == "__main__":
    sys.exit(main())
