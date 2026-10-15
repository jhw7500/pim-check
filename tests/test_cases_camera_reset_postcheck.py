from config import load_profile
from pim_check import PROFILES_DIR


def test_camera_reset_postcheck_is_read_only_and_complete():
    profile = load_profile(PROFILES_DIR, case="camera_reset_postcheck")
    assert "setup" not in profile
    assert profile["monitor"]["duration_sec"] == 0
    names = [item["name"] for item in profile["checks"]["custom_commands"]]
    assert names == [
        "cam-operate service active",
        "required video nodes present",
        "camera module refcounts nonnegative",
    ]
    assert profile["checks"]["custom_commands"] == [
        {
            "name": "cam-operate service active",
            "command": "systemctl is-active cam-operate.service",
            "expected": "active",
            "on_fail": "cam-operate.service is not active after reset/restore",
        },
        {
            "name": "required video nodes present",
            "command": "test -e /dev/video3 -a -e /dev/video4 && echo PRESENT || echo MISSING",
            "expected": "PRESENT",
            "on_fail": "required camera video nodes were not recreated",
        },
        {
            "name": "camera module refcounts nonnegative",
            "command": "N=$(lsmod | awk '$1==\"max9296\" || $1==\"imx8_media_dev\" {if ($3+0 < 0) n++} END {print n+0}'); echo $N",
            "expected": "0",
            "on_fail": "camera module has a negative reference count and requires reboot",
        },
    ]
