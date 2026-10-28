"""Contract for the read-only ORD/VCM integration sanity case.

Verifies configured runtime ports, not stale hard-coded port 10007/10009,
and avoids falsely advertising an OHT request/recording transaction.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from checks.custom import CustomCommandCheck, item_results
from config import load_profile
from plan import load_plan, resolve_cases

ROOT = Path(__file__).resolve().parents[1]
PROFILES = str(ROOT / "profiles")
PLAN = ROOT / "profiles/plans/ord_vcm_integration.yaml"


def test_integrated_case_is_read_only_and_never_reboots():
    config = load_profile(PROFILES, case="verify_ord_vcm_integration")
    assert not config.get("setup")
    assert not config.get("teardown")
    assert config["monitor"]["duration_sec"] == 0
    required = set(config["checks"]["processes"]["required"])
    assert {"gstApp", "BG_Check_for_pim", "chk_cam_operate", "ord", "vcm"} <= required


def test_plan_contains_only_explicit_read_only_case():
    plan = load_plan(str(PLAN))
    assert resolve_cases(plan, PROFILES) == [
        ("regression", "verify_ord_vcm_integration")
    ]
    assert plan.execution["case_retry"] == 0
    assert plan.execution["stop_on_fail"] is True


def test_endpoints_use_configured_ports_not_hardcoded_numbers():
    config = load_profile(PROFILES, case="verify_ord_vcm_integration")
    commands = {x["name"]: x["command"]
                for x in config["checks"]["custom_commands"]}
    assert "ORD.port_num" in commands["ORD uses configured TCP endpoint"]
    assert "VCM.port_num" in commands["VCM uses configured TCP endpoint"]
    for name in ("ORD uses configured TCP endpoint", "VCM uses configured TCP endpoint"):
        command = commands[name]
        assert 'tonumber' in command
        assert "timeout 3 bash -c" in command
        assert "10007" not in command and "10009" not in command
        assert "127.0.0.1/$P" in command
    parity = commands["ORD event_auto_remove agrees with camera"]
    assert "edgeconf_pim.json" in parity and "ord_vcm_conf.json" in parity
    assert 'has("event_auto_remove")' in parity
    assert "event_auto_remove ==" in parity


def test_every_custom_command_has_real_positive_expectation():
    config = load_profile(PROFILES, case="verify_ord_vcm_integration")
    commands = config["checks"]["custom_commands"]
    assert len(commands) >= 7
    assert all(x["expected"] in {"OK", "MATCH"} for x in commands)
    assert all(x.get("on_fail") for x in commands)


@pytest.mark.parametrize("failed_name,bad_output", [
    ("ORD/VCM config parsed", "INVALID_CONFIG"),
    ("ORD uses configured TCP endpoint", "INVALID_PORT"),
    ("ORD uses configured TCP endpoint", "TCP_CONNECT_FAILED"),
    ("VCM uses configured TCP endpoint", "INVALID_PORT"),
    ("VCM uses configured TCP endpoint", "TCP_CONNECT_FAILED"),
    ("ORD event_auto_remove agrees with camera", "MISMATCH"),
    ("ORD disk management config valid", "INVALID_DISK_POLICY"),
    ("VCM recording config valid", "INVALID_VCM_CONFIG"),
    ("VCM recording-state path available", "NO_RECORDING_STATE"),
] + [(name, output) for name in (
    "ORD/VCM config parsed", "ORD uses configured TCP endpoint",
    "VCM uses configured TCP endpoint", "ORD event_auto_remove agrees with camera",
    "ORD disk management config valid", "VCM recording config valid",
    "VCM recording-state path available",
) for output in ("", None)])
def test_each_failed_probe_prevents_aggregate_pass(failed_name, bad_output):
    config = load_profile(PROFILES, case="verify_ord_vcm_integration")
    commands = config["checks"]["custom_commands"]
    ssh = MagicMock()
    ssh.run.side_effect = [
        bad_output if c["name"] == failed_name
        else "MATCH" if c["name"] == "ORD event_auto_remove agrees with camera"
        else "OK"
        for c in commands
    ]
    check = CustomCommandCheck()
    data = check.collect(ssh, {"custom_commands": commands})
    passed, reason = check.validate(data, {})
    assert not passed
    assert failed_name in reason
    failed = [x["name"] for x in item_results(data) if not x["passed"]]
    assert failed == [failed_name]


def test_all_config_and_endpoint_probes_pass_with_matching_observation():
    config = load_profile(PROFILES, case="verify_ord_vcm_integration")
    commands = config["checks"]["custom_commands"]
    ssh = MagicMock()
    ssh.run.side_effect = [c["expected"] for c in commands]
    check = CustomCommandCheck()
    data = check.collect(ssh, {"custom_commands": commands})
    assert check.validate(data, {}) == (True, "OK")
    assert all(x["passed"] for x in item_results(data))


def test_existing_ord_liveness_check_is_not_vacuously_passed():
    config = load_profile(PROFILES, case="verify_ord_operation")
    spec = next(x for x in config["checks"]["custom_commands"]
                if x["name"] == "ORD PID stable over 2s")
    assert spec["expected"] == "STABLE"
    assert "kill -0" in spec["command"]
    assert spec.get("expected_min") is None
    ssh = MagicMock()
    check = CustomCommandCheck()
    ssh.run.return_value = "UNSTABLE"
    assert not check.validate(check.collect(ssh, {"custom_commands": [spec]}), {})[0]
    ssh.run.return_value = "STABLE"
    assert check.validate(check.collect(ssh, {"custom_commands": [spec]}), {})[0]
