"""Source-only system-level S0/S1 PIM read-only baseline contract.

The installed PIM package is NOT tested by this file. All SSH is mocked.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
from unittest.mock import MagicMock

import pytest

from checks.custom import CustomCommandCheck, item_results
from config import load_profile
from plan import load_plan, plan_runtime_profile, resolve_cases

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles"
PLAN = PROFILES / "plans" / "pim_system_live_readonly.yaml"
CASES = ("config_integrity", "verify_ord_vcm_integration",
         "verify_pim_system_live_pipeline")
MUTATING = re.compile(
    r"\b(?:rm|mv|cp|pkill|kill|umount|mount|reboot|shutdown|"
    r"i2ctransfer|i2cdetect|dd|mkfs|fdisk|sudo|curl|wget)\b"
    r"|\bsystemctl\s+(?:start|stop|restart|enable|disable)\b",
    re.IGNORECASE,
)


def pipeline():
    return load_profile(str(PROFILES), case="verify_pim_system_live_pipeline")


def commands():
    return pipeline()["checks"]["custom_commands"]


def test_system_readonly_plan_orders_package_config_and_media_strictly():
    plan = load_plan(str(PLAN))
    assert resolve_cases(plan, str(PROFILES)) == [
        ("regression", x) for x in CASES
    ]
    assert plan.execution["stop_on_fail"] is True
    assert plan.execution["case_retry"] == 0
    assert plan.execution["monitor_until_pass"] is True
    assert not plan.execution["skip_finalize_checks"]
    assert plan.gate["threshold_pass_rate"] == 1.0
    assert plan.gate["allow_known_issue"] is False
    assert plan.case_overrides["known_issues"] == []
    for case in CASES:
        effective = plan_runtime_profile(
            plan, load_profile(str(PROFILES), case=case)
        )
        assert effective["target"]["host"] == "192.168.214.4"
        assert not effective.get("setup"), case
        assert not effective.get("teardown"), case
        assert effective.get("known_issues") == []


def test_pipeline_requires_all_running_services_active_frames_and_recording():
    cfg = pipeline()
    assert cfg["monitor"]["duration_sec"] == 210
    assert {"gstApp", "ord", "vcm", "vsd", "BG_Check_for_pim",
            "chk_cam_operate"} <= set(cfg["checks"]["processes"]["required"])
    assert cfg["checks"]["frame_flow"]["enabled"] is True
    assert cfg["checks"]["frame_flow"]["sample_sec"] == 3
    assert cfg["checks"]["recording"]["session_progress"] == "required"


def test_package_inventory_has_installed_binaries_and_provenance_limitation():
    spec = commands()[0]
    assert "dpkg-query" in spec["command"]
    assert "pim-mp" in spec["command"]
    assert "sha256sum" in spec["command"]
    assert 'pgrep -x "$APP"' in spec["command"]
    assert "/proc/" in spec["command"] and "/exe" in spec["command"]
    for field in ("STATUS_RC:", "VERSION_RC:", "INSTALLED_SHA256:",
                  "PROCESS_COUNT:", "RUNNING_SHA256:", "EXE_LINK:"):
        assert field in spec["command"]
    assert "FAIL:PROCESS_COUNT:" in spec["command"]
    assert "FAIL:EXECUTABLE_MISMATCH:" in spec["command"]
    assert "for APP in ord vsd vcm" in spec["command"]
    assert '"/usr/local/bin/$APP"' in spec["command"]
    assert "expected_regex" in spec
    assert "source" in spec["on_fail"]


def test_config_propagates_across_all_channels():
    spec = commands()[2]
    cmd = spec["command"]
    for value in ("edgeconf_pim.json", "pim_runtime.json", "cam_width",
                  "cam_height", "fps", "recording_time", "enc", "bps",
                  "hflip", "vflip", "ae_on", "ae_gain", "awb",
                  "i2c2.ch0", "i2c2.ch1", "i2c1.ch2", "i2c1.ch3"):
        assert value in cmd
    assert "ACTIVE_RUNTIME_MISMATCH" in cmd
    assert spec["expected"] == "OK"


def test_same_boot_channel_video_metadata_and_srt_are_cross_correlated():
    spec = commands()[3]
    cmd = spec["command"]
    for value in ("journalctl -t gstApp -b", "Session complete:",
                  "for ch in $CH", "ffprobe", "avg_frame_rate", "bit_rate",
                  "recording_time", ".VCM.srt_enable", "SRT_MISSING",
                  "MP4_METADATA", "MP4_MISSING", "-mmin -5",
                  "*${S}00-data.srt"):
        assert value in cmd
    pattern = spec["expected_regex"]
    assert re.search(pattern, "MEDIA:{}\nVERDICT:MEDIA_OK")
    assert not re.search(pattern, "VERDICT:MEDIA_OK\nFAIL:AFTER_VERDICT")


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/bash"])
def test_remote_commands_are_nonmutating_and_shell_syntax_valid(shell):
    for spec in commands():
        cmd = spec["command"]
        assert MUTATING.search(cmd) is None, spec["name"]
        assert re.search(
            r"(?:^|\s)>{1,2}\s*(?!/dev/null(?:\s|$))\S+", cmd
        ) is None
        result = subprocess.run(
            [shell, "-n", "-c", cmd],
            capture_output=True, text=True, timeout=10, check=False,
        )
        assert result.returncode == 0, (spec["name"], result.stderr)


def positive():
    return [
        "PACKAGE:pim-mp STATUS:<install ok installed>\nVERDICT:PACKAGE_IDENTITY_OK",
        "OK", "OK", "MEDIA:{}\nVERDICT:MEDIA_OK",
    ]


def test_mocked_aggregate_exercises_all_subsystems():
    ssh = MagicMock()
    ssh.run.side_effect = positive()
    check = CustomCommandCheck()
    result = check.collect(ssh, {"custom_commands": commands()})
    assert check.validate(result, {}) == (True, "OK")
    assert all(row["passed"] for row in item_results(result))


@pytest.mark.parametrize("failed_name,bad_output", [
    ("installed pim-mp package and binary identity observed",
     "FAIL:PACKAGE_NOT_INSTALLED"),
    ("installed pim-mp package and binary identity observed", None),
    ("owner cam/ORD/VSD systemd services active",
     "FAIL:SERVICE:ord-operate.service"),
    ("edgeconf camera settings reflected in merged runtime",
     "FAIL:ACTIVE_RUNTIME_MISMATCH"),
    ("same-current-boot finalized channel MP4 and configured SRT",
     "NEED_2_FINALIZES_AFTER_BOOT"),
    ("same-current-boot finalized channel MP4 and configured SRT",
     "FAIL:MP4_METADATA:ch0:20261103_1200"),
    ("same-current-boot finalized channel MP4 and configured SRT",
     "FAIL:SRT_MISSING"),
])
def test_absent_or_failed_one_subsystem_prevents_aggregate_pass(
    failed_name, bad_output,
):
    specs = commands()
    responses = positive()
    for i, spec in enumerate(specs):
        if spec["name"] == failed_name:
            responses[i] = bad_output
    ssh = MagicMock()
    ssh.run.side_effect = responses
    check = CustomCommandCheck()
    actual = check.collect(ssh, {"custom_commands": specs})
    passed, reason = check.validate(actual, {})
    assert passed is False
    assert failed_name in reason
    assert [r["name"] for r in item_results(actual) if not r["passed"]] == [
        failed_name
    ]


def test_plan_is_observation_not_fault_or_complete_release_acceptance():
    body = PLAN.read_text(encoding="utf-8")
    assert "READ-ONLY" in body
    for forbidden in ("fault_gstapp_crash", "fault_sd_unmounted",
                      "qa_recovery_acceptance", "fhd_4ch"):
        assert forbidden not in body


@pytest.mark.parametrize("mode,expected", [
    ("ok", "VERDICT:PACKAGE_IDENTITY_OK"),
    ("missing_ord", "FAIL:PROCESS_COUNT:ord"),
    ("duplicate_ord", "FAIL:PROCESS_COUNT:ord"),
    ("changed_ord", "FAIL:EXECUTABLE_MISMATCH:ord"),
    ("missing_binary_ord", "FAIL:EXECUTABLE_MISMATCH:ord"),
])
def test_installed_binary_matches_actual_process_executable_fail_closed(
    tmp_path, mode: str, expected: str,
):
    """All commands here are fake PATH stubs; NO board or live process reads."""
    import os

    bin_dir = tmp_path / "fake-commands"
    bin_dir.mkdir()
    stubs = {
        "dpkg-query": """#!/bin/sh
case "$2" in
  *Status*) printf 'install ok installed';;
  *Version*) printf '0.6.3.2+jhw.camera19';;
  *) exit 1;;
esac
""",
        "pgrep": """#!/bin/sh
case "$2" in
  ord)
    [ "$MOCK_PID_CASE" = missing_ord ] && exit 1
    printf '1001\\n'
    [ "$MOCK_PID_CASE" = duplicate_ord ] && printf '1004\\n';;
  vsd) printf '1002\\n';;
  vcm) printf '1003\\n';;
  *) exit 1;;
esac
exit 0
""",
        "readlink": """#!/bin/sh
case "$1" in
  /proc/1001/exe|/proc/1004/exe) printf '/usr/local/bin/ord\\n';;
  /proc/1002/exe) printf '/usr/local/bin/vsd\\n';;
  /proc/1003/exe) printf '/usr/local/bin/vcm\\n';;
  *) exit 1;;
esac
""",
        "sha256sum": """#!/bin/sh
case "$1" in
  /usr/local/bin/ord|/proc/1001/exe) SUFFIX=1;;
  /usr/local/bin/vsd|/proc/1002/exe) SUFFIX=2;;
  /usr/local/bin/vcm|/proc/1003/exe) SUFFIX=3;;
  *) exit 1;;
esac
if [ "$MOCK_PID_CASE" = missing_binary_ord ] &&
   [ "$1" = /usr/local/bin/ord ]; then exit 1; fi
if [ "$MOCK_PID_CASE" = changed_ord ] &&
   [ "$1" = /proc/1001/exe ]; then SUFFIX=4; fi
printf '%063d%s  %s\\n' 0 "$SUFFIX" "$1"
""",
    }
    for name, body in stubs.items():
        executable = bin_dir / name
        executable.write_text(body, encoding="utf-8")
        executable.chmod(0o700)

    result = subprocess.run(
        ["/bin/bash", "-c", commands()[0]["command"]],
        env={**os.environ, "PATH": str(bin_dir) + ":" + os.environ["PATH"],
             "MOCK_PID_CASE": mode},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    for app in ("ord", "vsd", "vcm"):
        assert f"APP:{app} INSTALLED_PATH:" in result.stdout
    if mode == "ok":
        assert result.stdout.strip().endswith(expected)
        assert re.search(commands()[0]["expected_regex"], result.stdout.strip())
        assert not re.search(
            commands()[0]["expected_regex"],
            result.stdout.strip() + "\nFAIL:AFTER_VERDICT")
    else:
        assert any(line.startswith(expected) for line in result.stdout.splitlines())
        assert result.stdout.strip().endswith("VERDICT:PACKAGE_IDENTITY_FAIL")
        assert not re.search(commands()[0]["expected_regex"], result.stdout.strip())


