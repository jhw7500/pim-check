"""board_error_detect kernel error accounting: explicit origin and fail-closed evidence.

These cases replay a real 2026-10-26 boot-time MCP4018 failure pattern
against the actual YAML shell command. No board, SSH, reboot or SD files.
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "cases" / "board_error_detect.yaml"
LOG_PATH = "/var/log/cantops/kern.log"
ANCHOR_PATH = "/tmp/pim_check_anchor"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
BOOT_TIME = "2026-10-26 08:53:00"
BOOT_ID = "5c1762b0-374c-4b2d-adfb-b8b5419b0d80"

ERRORS = [
    "2026-10-26 08:53:27.381 kernel[err][   27.251533] [I2C:2][max9296.c:1456] ch0 MCP4018(0x2f) write fail: wiper=0x10 ret=-6",
    "2026-10-26 08:53:27.633 kernel[err][   27.251891] [I2C:2][max9296.c:3468] ch0 single applied fail (ret=-6)",
    "2026-10-26 08:53:28.245 kernel[err][   28.115421] [I2C:2][max9296.c:1456] ch0 MCP4018(0x2f) write fail: wiper=0x10 ret=-6",
    "2026-10-26 08:53:29.245 kernel[err][   28.115791] [I2C:2][max9296.c:3468] ch0 single applied fail (ret=-6)",
]
NOISE = (
    "2026-10-26 08:53:26.001 kernel[err][    1.100000] "
    "imx8-pcie-phy failed to get clock"
)


def _spec():
    profile = yaml.safe_load(PROFILE.read_text())
    rows = [row for row in profile["checks"]["custom_commands"]
            if "COUNT=" in row["command"] and LOG_PATH in row["command"]]
    assert len(rows) == 1, "커널 에러 계수 체크 한 개를 유지해야 한다"
    return rows[0]


def _run(source: str | None, *, anchor: str | None = None,
         uptime: str = BOOT_TIME, boot_id: str = BOOT_ID):
    with tempfile.TemporaryDirectory() as d:
        directory = pathlib.Path(d)
        log = directory / "kern.log"
        aid = directory / "anchor"
        bid = directory / "boot_id"
        bid.write_text(boot_id + "\n")
        if source is not None:
            log.write_text(source)
        if anchor is not None:
            aid.write_text(anchor)
        command = (_spec()["command"]
                   .replace(LOG_PATH, str(log))
                   .replace(ANCHOR_PATH, str(aid))
                   .replace(BOOT_ID_PATH, str(bid))
                   .replace("uptime -s",
                            "printf '%s\\n' '" + uptime + "'"))
        return subprocess.run(["sh", "-c", command],
                              capture_output=True, text=True, timeout=8)


def test_boot_fallback_annotates_real_failure_and_does_not_suppress_it():
    r = _run("\n".join([NOISE, *ERRORS]) + "\n")
    assert r.returncode == 0, r.stderr
    output = r.stdout.strip()
    assert output.startswith("COUNT=4 SCOPE=BOOT_FALLBACK START=" + BOOT_TIME)
    assert "FIRST=" + ERRORS[0][:180] in output
    assert "LAST=" + ERRORS[-1][:180] in output
    assert len(output) < 600


def test_current_boot_session_anchor_scopes_only_after_anchor():
    r = _run("\n".join(ERRORS) + "\n",
             anchor="2026-10-26 08:53:28\n" + BOOT_ID + "\n")
    assert r.returncode == 0
    assert r.stdout.startswith("COUNT=1 SCOPE=SESSION START=2026-10-26 08:53:28")


def test_wrong_boot_anchor_is_explicit_boot_fallback():
    r = _run("\n".join(ERRORS) + "\n",
             anchor="2026-10-26 08:53:28\nother-boot-id\n")
    assert r.returncode == 0
    assert "COUNT=4 SCOPE=BOOT_FALLBACK" in r.stdout


def test_no_matching_errors_is_exact_zero():
    r = _run(NOISE + "\n2026-10-26 08:53:29.001 kernel[notice] streaming OK\n")
    assert r.returncode == 0
    assert r.stdout.strip() == "0"


def test_missing_or_empty_kernel_log_fails_closed():
    for source in (None, ""):
        r = _run(source)
        assert r.returncode == 0
        assert r.stdout.strip() == "NO_SOURCE"


def test_invalid_boot_wall_clock_fails_closed():
    for uptime in ("", "2026/10/26 08:53:00", "bad-time"):
        r = _run("\n".join(ERRORS) + "\n", uptime=uptime)
        assert r.returncode == 0
        assert r.stdout.strip() == "INVALID_START_TIME"


def test_all_errors_counted_but_sample_is_bounded():
    huge = ("2026-10-26 08:53:29.001 kernel[err] max9296.c:1456 write fail "
            + "x" * 3000)
    r = _run("\n".join([huge] * 100) + "\n")
    assert r.returncode == 0
    assert r.stdout.startswith("COUNT=100 SCOPE=BOOT_FALLBACK")
    assert len(r.stdout) < 600


def test_invalid_source_and_real_errors_cannot_pass_expected_zero():
    from checks.custom import CustomCommandCheck

    spec = _spec()
    check = CustomCommandCheck()
    for output in ("NO_SOURCE", "INVALID_START_TIME",
                   "COUNT=4 SCOPE=BOOT_FALLBACK START=" + BOOT_TIME):
        item = {
            "name": spec["name"], "command": spec["command"],
            "output": output, "expected": "0", "expected_min": None,
            "expected_max": None, "expected_regex": None,
            "on_fail": spec["on_fail"],
        }
        ok, reason = check.validate({"results": [item], "skipped": False}, {})
        assert ok is False
        assert output in reason
