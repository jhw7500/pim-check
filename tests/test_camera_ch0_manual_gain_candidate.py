"""Single-purpose camera gain case: schema/coverage only, never runs a board."""
from pathlib import Path

from config import load_profile


ROOT = Path(__file__).resolve().parents[1]
CASE = "camera_ch0_manual_gain_1024_fhd"


def test_ch0_manual_gain_case_touches_only_expected_camera_settings():
    config = load_profile(str(ROOT / "profiles"), case=CASE)
    assert config["target"]["host"] == "192.168.214.4"
    changes = config["setup"]["edgeconf_changes"]
    assert changes[".VHL_CAM.i2c2.ch0.ae_on"] is False
    assert changes[".VHL_CAM.i2c2.ch0.ae_gain"] == 1024
    assert changes[".VHL_CAM.cam_width"] == 1920
    assert changes[".VHL_CAM.cam_height"] == 1080
    assert changes[".VHL_CAM.fps"] == 15
    assert changes[".VHL_CAM.enc"] == "h265"
    assert changes[".VHL_CAM.i2c2.ch0.bps"] == [1024, 1024]
    for channel, expected in (
        ("i2c2.ch0", True),
        ("i2c2.ch1", False),
        ("i2c1.ch2", False),
        ("i2c1.ch3", False),
    ):
        assert changes[f".VHL_CAM.{channel}.enable"] is expected
        assert changes[f".VHL_CAM.{channel}.led_flash.enable"] is False
    assert changes[".VHL_CAM.i2c2.ch0.vflip"] is False
    assert changes[".VHL_CAM.i2c2.ch0.hflip"] is True
    assert changes[".VHL_CAM.i2c2.ch0.awb"] == "auto"
    assert "ord_vcm_changes" not in config["setup"]
    assert config["setup"]["reboot_after"] is True


def test_gain_case_has_actual_frame_and_video_checks_not_just_config():
    config = load_profile(str(ROOT / "profiles"), case=CASE)
    assert config["checks"]["frame_flow"]["enabled"] is True
    assert config["checks"]["recording"]["expected_channels"] == 1
    commands = config["checks"]["custom_commands"]
    assert len(commands) == 3
    assert all(row["expected"] == "OK" for row in commands)
    text = "\n".join(row["command"] for row in commands)
    assert "ae_gain == 1024" in text
    assert "pim_runtime.json" in text
    assert "ffprobe" in text and "avg_frame_rate" in text
    assert "NO_POST_BOOT_MP4" in text
    assert "i2ctransfer" not in text and "i2cdetect" not in text
    assert "systemctl" not in text and "rm -f" not in text


def test_gain_case_does_not_claim_image_effect_or_ready_board_execution():
    config = load_profile(str(ROOT / "profiles"), case=CASE)
    assert "manual_gain" in config["tags"]
    description = config["description"].lower()
    assert "does not prove" in description
    assert "image comparison" in description
    assert config["setup"]["stabilize_sec"] >= 60
