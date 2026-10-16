"""Read-only camera13 postcheck acceptance contract."""
import subprocess

from config import load_profile
from pim_check import PROFILES_DIR


def test_camera_reset_postcheck_fails_closed_with_owner_and_history():
    profile = load_profile(PROFILES_DIR, case="camera_reset_postcheck")
    assert "setup" not in profile
    assert profile["monitor"]["duration_sec"] == 0
    entries = profile["checks"]["custom_commands"]
    names = [item["name"] for item in entries]
    assert names == [
        "cam-operate service active",
        "owner ACTIVE and no queued recovery",
        "required video nodes present",
        "camera kernel modules loaded",
        "loaded MAX9296 version is 2.12",
        "latest hardware reset audited for same owner",
    ]
    assert [entry["expected"] for entry in entries] == [
        "active", "READY", "PRESENT", "LOADED", "MATCH", "VERIFIED",
    ]
    assert entries[0]["command"] == "systemctl is-active cam-operate.service"

    owner = entries[1]["command"]
    assert "cam-recoveryctl status --json" in owner
    for item in ("ACTIVE", ".pending == null", ".active == null"):
        assert item in owner

    modules = entries[3]["command"]
    assert "/sys/module/max9296" in modules
    assert "/sys/module/imx8_media_dev" in modules
    assert "lsmod | awk" not in modules

    loaded_version = entries[4]["command"]
    assert "/sys/module/max9296/version" in loaded_version
    assert '"2.12"' in loaded_version
    history = entries[5]["command"]
    for value in (
        "/tmp/pim_check_recovery_audit.json",
        "/var/lib/pim-camera/recovery/history/",
        "/run/pim-camera/owner.json",
        ".request_id",
        ".checked_at",
        ".invocation_id",
        ".proc_start_time",
        ".boot_id",
        "camera_hard_reset",
        "reboot_fallback",
        'source == "pim-check"',
    ):
        assert value in history


def test_postcheck_remote_shell_commands_parse():
    """The collected YAML commands must be syntactically valid POSIX shell."""
    profile = load_profile(PROFILES_DIR, case="camera_reset_postcheck")
    for item in profile["checks"]["custom_commands"]:
        proc = subprocess.run(
            ["sh", "-n", "-c", item["command"]],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, (item["name"], proc.stderr)


def test_strict_hard_reset_acceptance_is_opted_in_only_for_camera_transitions():
    assert load_profile(
        PROFILES_DIR, case="multi_4ch_720p")["setup"]["require_hard_reset"] is True
    assert load_profile(
        PROFILES_DIR, case="multi_1ch_0_fhd")["setup"]["require_hard_reset"] is True
    assert "require_hard_reset" not in load_profile(
        PROFILES_DIR, case="720p_2ch")["setup"]
    assert load_profile(
        PROFILES_DIR, case="720p_2ch")["setup"]["ord_vcm_changes"]
