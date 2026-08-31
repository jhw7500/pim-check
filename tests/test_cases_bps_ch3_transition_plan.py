"""ch3 BPS 간헐 실패의 최소 전환 재현 plan 계약."""
from __future__ import annotations

from pathlib import Path

from plan import load_plan, resolve_cases


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = PROJECT_ROOT / "profiles"
PLAN_PATH = PROFILES_DIR / "plans" / "bps_ch3_transition.yaml"


def test_bps_ch3_transition_plan_runs_only_the_smoke_prefix_in_order():
    plan = load_plan(str(PLAN_PATH))

    assert resolve_cases(plan, str(PROFILES_DIR)) == [
        ("regression", "720p_2ch"),
        ("regression", "720p_4ch"),
    ]


def test_bps_ch3_transition_plan_preserves_the_first_raw_failure():
    plan = load_plan(str(PLAN_PATH))

    assert plan.execution["case_retry"] == 0
    assert plan.execution["stop_on_fail"] is False
    assert plan.execution["monitor_until_pass"] is True
    assert plan.gate["allow_known_issue"] is False
    assert [report["format"] for report in plan.reports] == ["json"]
