"""CustomCommandCheck expected_regex is a real failure gate, not presentation metadata.

Regression: fault_gstapp_crash used expected_regex but collect()/validate() silently
ignored it. A literal NOT_RESPAWNED / NOT_HEALTHY reply therefore became PASS.
All tests use mocked SSH; no camera, OHT, SD, or board access.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from checks.custom import CustomCommandCheck, failing_items, item_results
from config import load_profile

PROFILES = str(Path(__file__).resolve().parents[1] / "profiles")


@pytest.mark.parametrize("item_name,bad,good", [
    ("gstApp respawned within 60s", "NOT_RESPAWNED", "RESPAWNED:10s"),
    ("cam_state recovers to healthy", "NOT_HEALTHY:state=recovering", "HEALTHY:15s"),
])
def test_real_gstapp_fault_regex_rejects_nonrecovery(item_name, bad, good):
    profile = load_profile(PROFILES, case="fault_gstapp_crash")
    spec = next(s for s in profile["checks"]["custom_commands"] if s["name"] == item_name)
    check = CustomCommandCheck()
    ssh = MagicMock()

    ssh.run.return_value = bad
    failed_data = check.collect(ssh, {"custom_commands": [spec]})
    assert failed_data["results"][0]["expected_regex"] == spec["expected_regex"]
    assert not check.validate(failed_data, {})[0]
    assert item_results(failed_data)[0]["passed"] is False
    assert item_results(failed_data)[0]["expected"].startswith("regex: ")
    assert failing_items(failed_data)[0][0] == item_name

    ssh.run.return_value = good
    success_data = check.collect(ssh, {"custom_commands": [spec]})
    assert check.validate(success_data, {}) == (True, "OK")
    assert item_results(success_data)[0]["passed"] is True


@pytest.mark.parametrize("output,pattern", [
    (None, r"^HEALTHY:"),
    ("", r"^HEALTHY:"),
    ("HEALTHY:10s", "["),
    ("HEALTHY:10s", ""),
    ("NOT_HEALTHY", r"^HEALTHY:"),
])
def test_regex_missing_output_or_invalid_pattern_never_passes(output, pattern):
    check = CustomCommandCheck()
    spec = {"name": "health", "command": "state", "expected_regex": pattern,
            "on_fail": "did not recover"}
    ssh = MagicMock()
    ssh.run.return_value = output
    data = check.collect(ssh, {"custom_commands": [spec]})
    assert not check.validate(data, {})[0]
    assert not item_results(data)[0]["passed"]
    assert "did not recover" in check.validate(data, {})[1]


def test_existing_exact_and_minimum_results_are_not_reinterpreted_as_regex():
    check = CustomCommandCheck()
    ssh = MagicMock()
    ssh.run.side_effect = ["OK", "11"]
    data = check.collect(ssh, {"custom_commands": [
        {"name": "exact", "command": "echo OK", "expected": "OK"},
        {"name": "min", "command": "echo 11", "expected_min": 10},
    ]})
    assert check.validate(data, {}) == (True, "OK")
    assert all(r["passed"] for r in item_results(data))


def test_dangerous_regex_command_is_blocked_and_never_passes():
    check = CustomCommandCheck()
    ssh = MagicMock()
    data = check.collect(ssh, {"custom_commands": [
        {"name": "blocked", "command": "rm -rf /tmp/test",
         "expected_regex": r"^OK$", "on_fail": "blocked"}
    ]})
    ssh.run.assert_not_called()
    assert check.validate(data, {})[0] is False
    assert item_results(data)[0]["passed"] is False
