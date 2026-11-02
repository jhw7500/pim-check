"""Camera functional smoke: existing generated cases, no physical board action."""
from pathlib import Path

from config import load_profile
from plan import load_plan, plan_runtime_profile, resolve_cases


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles"
PLAN = PROFILES / "plans" / "smoke_camera_functional.yaml"
EXPECTED = [
    "gen_fhd_ch0_hflip_off",
    "gen_fhd_ch0_ae_on",
    "gen_fhd_ch0_awb_off",
]


def _runtime(case):
    plan = load_plan(str(PLAN))
    return plan_runtime_profile(plan, load_profile(str(PROFILES), case=case))


def test_plan_reuses_exact_existing_generated_camera_cases():
    plan = load_plan(str(PLAN))
    result = resolve_cases(plan, str(PROFILES))
    assert [name for _, name in result] == EXPECTED
    assert all(section == "regression" for section, _ in result)
    assert plan.execution["stop_on_fail"] is True
    assert plan.execution["case_retry"] == 0
    assert plan.execution["monitor_until_pass"] is True
    assert plan.execution["skip_finalize_checks"] is False
    assert not plan.gate["allow_known_issue"]


def test_each_effective_case_checks_real_frames_and_recording_without_forced_i2c():
    plan = load_plan(str(PLAN))
    for case in EXPECTED:
        original = load_profile(str(PROFILES), case=case)
        # The generated cases still have their original, force-I2C commands.
        assert "i2ctransfer -f" in str(
            original.get("checks", {}).get("custom_commands", []))
        runtime = plan_runtime_profile(plan, original)
        assert runtime["target"]["host"] == "192.168.214.4"
        assert runtime["checks"]["max9296_abi"]["expected_version"] is None
        assert runtime["checks"]["frame_flow"]["enabled"] is True
        assert runtime["checks"]["recording"]["expected_channels"] == 1
        assert runtime["checks"]["recording"]["session_progress"] == "required"
        commands = runtime["checks"]["custom_commands"]
        assert len(commands) == 2
        assert all(item["expected"] == "OK" for item in commands)
        assert "pim_runtime.json" in commands[0]["command"]
        assert "ffprobe" in commands[1]["command"]
        assert "avg_frame_rate" in commands[1]["command"]
        assert "bit_rate" in commands[1]["command"]
        assert "journalctl -t gstApp -b" in commands[1]["command"]
        assert "NEED_2_FINALIZES_AFTER_BOOT" in commands[1]["command"]
        assert "i2ctransfer" not in str(commands)
        assert "i2cdetect" not in str(commands)
        assert "systemctl" not in str(commands)
        assert runtime["monitor"]["duration_sec"] == 210
        assert runtime["setup"]["stabilize_sec"] == 120
        assert runtime["setup"]["reboot_after"] is True


def test_camera_setup_does_not_turn_on_missing_mcp_or_other_channels():
    for case in EXPECTED:
        changes = _runtime(case)["setup"]["edgeconf_changes"]
        for ch, expected in (
            ("i2c2.ch0", True),
            ("i2c2.ch1", False),
            ("i2c1.ch2", False),
            ("i2c1.ch3", False),
        ):
            assert changes[f".VHL_CAM.{ch}.enable"] is expected
            assert changes[f".VHL_CAM.{ch}.led_flash.enable"] is False
        assert changes[".VHL_CAM.cam_width"] == 1920
        assert changes[".VHL_CAM.cam_height"] == 1080
        assert changes[".VHL_CAM.fps"] == 15
        assert changes[".VHL_CAM.enc"] == "h265"
        assert "ord_vcm_changes" not in _runtime(case)["setup"]


def test_original_smoke_plan_unchanged_and_high_rate_4ch_not_selected():
    original = load_plan(str(PROFILES / "plans" / "smoke.yaml"))
    assert "720p_4ch" in [name for _, name in resolve_cases(original, str(PROFILES))]
    selected = [name for _, name in resolve_cases(
        load_plan(str(PLAN)), str(PROFILES))]
    assert "720p_4ch" not in selected
    assert "fhd_4ch" not in selected
