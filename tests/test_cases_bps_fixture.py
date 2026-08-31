from __future__ import annotations

from config import load_profile


def test_bps_case_is_a_complete_deterministic_single_channel_fixture() -> None:
    """The gate must control every known bitrate input instead of inheriting drift."""
    changes = load_profile("profiles", "multi_1ch_0_720p")["setup"]["edgeconf_changes"]

    assert changes[".VHL_CAM.enc"] == "h265"
    # QP [0, 0] is the empirically demonstrated target-error fix.
    assert changes[".VHL_CAM.i2c2.ch0.qp_min"] == [0, 0]
    assert changes[".VHL_CAM.i2c2.ch0.qp_max"] == [0, 0]
    # quant auto is controlled to eliminate drift, not asserted as the cause.
    assert changes[".VHL_CAM.i2c2.ch0.quant"] == [-1, -1]
    # Profile zero is likewise a controlled fixture input.
    assert changes[".VHL_CAM.i2c2.ch0.profile"] == [0, 0]
    assert changes[".VHL_CAM.cam_width"] == 1280
    assert changes[".VHL_CAM.cam_height"] == 720
    assert changes[".VHL_CAM.fps"] == 30
    assert changes[".VHL_CAM.muxer"] == "mp4"
    assert changes[".VHL_CAM.recording_time"] == 1
    assert changes[".VHL_CAM.capture.enable"] is False
    assert changes[".VHL_CAM.i2c2.exp_time"] == 5000

    enabled = {
        key: value
        for key, value in changes.items()
        if key.endswith(".enable") and ".ch" in key
    }
    assert enabled == {
        ".VHL_CAM.i2c2.ch0.enable": True,
        ".VHL_CAM.i2c2.ch1.enable": False,
        ".VHL_CAM.i2c1.ch2.enable": False,
        ".VHL_CAM.i2c1.ch3.enable": False,
    }
