"""Existing-case ch1/2/3 live-functional plan — offline profile contract."""
from pathlib import Path

from config import load_profile
from plan import load_plan, plan_runtime_profile, resolve_cases


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles"
PATH = PROFILES / "plans" / "channel_camera_functional.yaml"
CASES = ("gen_fhd_1ch_ch1", "gen_fhd_1ch_ch2", "gen_fhd_1ch_ch3")


def test_existing_single_channel_cases_are_ordered_and_stop_on_fail():
    plan = load_plan(str(PATH))
    assert [case for _, case in resolve_cases(plan, str(PROFILES))] == list(CASES)
    assert plan.execution["stop_on_fail"] is True
    assert plan.execution["case_retry"] == 0
    assert not plan.execution["skip_finalize_checks"]


def test_actual_frame_and_recording_checks_replace_forced_register_probes():
    plan = load_plan(str(PATH))
    for i, case in enumerate(CASES, start=1):
        original = load_profile(str(PROFILES), case=case)
        assert "i2cdetect" in str(original.get("checks", {}).get("custom_commands"))
        runtime = plan_runtime_profile(plan, original)
        checks = runtime["checks"]
        assert runtime["target"]["host"] == "192.168.214.4"
        assert checks["max9296_abi"]["expected_version"] is None
        assert checks["frame_flow"]["enabled"] is True
        assert checks["recording"]["expected_channels"] == 1
        assert checks["recording"]["session_progress"] == "required"
        assert len(checks["custom_commands"]) == 1
        command = checks["custom_commands"][0]["command"]
        assert "pim_runtime.json" in command and "bps" in command
        assert "i2ctransfer" not in command and "i2cdetect" not in command
        changes = runtime["setup"]["edgeconf_changes"]
        for ch in range(4):
            bus = "i2c2" if ch <= 1 else "i2c1"
            assert changes[f".VHL_CAM.{bus}.ch{ch}.enable"] is (ch == i)
            assert changes[f".VHL_CAM.{bus}.ch{ch}.led_flash.enable"] is False
