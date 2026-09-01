"""ch3 BPS 간헐 실패의 최소 전환 재현 plan 계약."""
from __future__ import annotations

from pathlib import Path

from config import load_profile
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


def test_bps_ch3_transition_uses_the_deployed_max9296_version():
    for case_name in ("720p_2ch", "720p_4ch"):
        profile = load_profile(str(PROFILES_DIR), case_name)

        assert profile["checks"]["max9296_abi"]["expected_version"] == "2.10"


def test_bps_ch3_transition_pins_and_checks_qp_auto_for_every_active_channel():
    cases = {
        "720p_2ch": {
            "ch0": ".VHL_CAM.i2c2.ch0",
            "ch1": ".VHL_CAM.i2c2.ch1",
        },
        "720p_4ch": {
            "ch0": ".VHL_CAM.i2c2.ch0",
            "ch1": ".VHL_CAM.i2c2.ch1",
            "ch2": ".VHL_CAM.i2c1.ch2",
            "ch3": ".VHL_CAM.i2c1.ch3",
        },
    }

    for case_name, channels in cases.items():
        profile = load_profile(str(PROFILES_DIR), case_name)
        changes = profile["setup"]["edgeconf_changes"]
        commands = {
            command["name"]: command
            for command in profile["checks"]["custom_commands"]
        }

        for channel, path in channels.items():
            assert changes[f"{path}.qp_min"] == [0, 0]
            assert changes[f"{path}.qp_max"] == [0, 0]
            assert changes[f"{path}.quant"] == [-1, -1]

            gate = commands[f"{channel} BPS QP auto precondition"]
            assert gate["expected"] == "OK"
            assert path in gate["command"]
            assert "FAIL:QP_NOT_AUTO" in gate["command"]
