"""Existing fault cases must enforce numeric upper limits, not >=0 tautologies.

The tests exercise real YAML with mocked SSH. No board operation is performed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from checks.custom import CustomCommandCheck, item_results
from config import load_profile

PROFILES = str(Path(__file__).resolve().parents[1] / "profiles")


@pytest.mark.parametrize("case,name,good,bad,limit", [
    ("fault_system_hang", "Load average 1min < 8.0", "7.98", "8.00", 7.99),
    ("fault_system_hang", "D state processes < 3", "2", "3", 2),
    ("fault_memory_leak", "gstApp RSS < 500MB", "510000", "512000", 511999),
    ("fault_time_sync", "System-RTC time drift < 60s", "59", "60", 59),
    ("verify_rtc_sync", "System-RTC 시간 차이 < 10초", "9", "10", 9),
    ("verify_resolution_hd", "HD 온도 93도 이하 (FHD 비교 아님)", "92", "94", 93),
])
def test_upper_bounds_enforced_from_real_profiles(case, name, good, bad, limit):
    profile = load_profile(PROFILES, case=case)
    spec = next(x for x in profile["checks"]["custom_commands"] if x["name"] == name)
    assert spec["expected_max"] == limit
    ssh = MagicMock()
    check = CustomCommandCheck()
    for actual, expected_result in ((good, True), (bad, False)):
        ssh.run.return_value = actual
        data = check.collect(ssh, {"custom_commands": [spec]})
        assert check.validate(data, {})[0] is expected_result
        assert item_results(data)[0]["passed"] is expected_result


def test_rtc_read_failure_is_not_falsely_returned_as_zero_drift():
    spec = next(x for x in load_profile(PROFILES, case="fault_time_sync")["checks"]["custom_commands"]
                if x["name"] == "System-RTC time drift < 60s")
    assert "9999 if r.returncode!=0" in spec["command"]
    assert spec["expected_max"] == 59


def test_dns_presence_requires_at_least_one_nameserver():
    spec = next(x for x in load_profile(PROFILES, case="verify_network_used")["checks"]["custom_commands"]
                if x["name"] == "DNS 해석 가능")
    assert spec["expected_min"] == 1
    ssh = MagicMock()
    ssh.run.return_value = "0"
    check = CustomCommandCheck()
    assert not check.validate(check.collect(ssh, {"custom_commands": [spec]}), {})[0]


def test_recent_recording_is_based_on_age_not_any_historic_unix_timestamp():
    spec = next(x for x in load_profile(PROFILES, case="fault_sd_recovery")["checks"]["custom_commands"]
                if x["name"] == "Recent recording file exists (last 10min)")
    assert "-mmin -10" in spec["command"]
    assert "/mnt/sd_cam" in spec["command"]
    assert "/dev/shm/recordings" in spec["command"]
    assert spec["expected"] == "RECENT"
    check = CustomCommandCheck()
    ssh = MagicMock()
    for observed, allowed in (("RECENT", True), ("NO_RECENT_RECORDING", False)):
        ssh.run.return_value = observed
        assert check.validate(check.collect(ssh, {"custom_commands": [spec]}), {})[0] is allowed


@pytest.mark.parametrize("case,name", [
    ("fault_high_cpu", "System load average (진단용, 비게이팅)"),
    ("fault_network_down", "RTSP port listener count (diagnostic; optional)"),
])
def test_optional_diagnostic_items_are_explicitly_not_a_fake_min_zero_gate(case, name):
    cfg = load_profile(PROFILES, case=case)
    spec = next(x for x in cfg["checks"]["custom_commands"] if x["name"] == name)
    assert all(spec.get(k) is None for k in ("expected", "expected_min", "expected_max", "expected_regex"))
