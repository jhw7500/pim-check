"""Upper/lower numeric limits must fail when observed values are out of range.

All samples are mocked; no SSH connection or hardware required.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from checks.custom import CustomCommandCheck, failing_items, item_results


@pytest.mark.parametrize("output,success", [
    ("7.99", True),
    ("8.00", False),
    ("14", False),
    ("", False),
    (None, False),
    ("NaN", False),
    ("inf", False),
    ("-inf", False),
    ("unavailable", False),
])
def test_expected_max_enforces_strict_8_load_limit(output, success):
    check = CustomCommandCheck()
    ssh = MagicMock()
    ssh.run.return_value = output
    spec = {"name": "load < 8", "command": "cat /proc/loadavg",
            "expected_max": 7.99, "on_fail": "overloaded"}
    data = check.collect(ssh, {"custom_commands": [spec]})
    result, reason = check.validate(data, {})
    assert result is success
    item = item_results(data)[0]
    assert item["expected"] == "<= 7.99"
    assert item["passed"] is success
    assert bool(failing_items(data)) is not success
    if not success:
        assert "overloaded" in reason


@pytest.mark.parametrize("output,success", [
    ("0", True),
    ("2", True),
    ("3", False),
    ("-1", False),
    ("true", False),
])
def test_numeric_interval_combines_both_bounds(output, success):
    check = CustomCommandCheck()
    ssh = MagicMock()
    ssh.run.return_value = output
    data = check.collect(ssh, {"custom_commands": [{
        "name": "D-state count", "command": "ps", "expected_min": 0,
        "expected_max": 2, "on_fail": "blocked tasks"
    }]})
    assert check.validate(data, {})[0] is success
    item = item_results(data)[0]
    assert item["passed"] is success
    assert item["expected"] == ">= 0, <= 2"


def test_missing_or_nonfinite_threshold_fails_instead_of_vacuous_pass():
    check = CustomCommandCheck()
    ssh = MagicMock()
    ssh.run.return_value = "0"
    for limit in ("not-a-number", float("nan"), float("inf"), True):
        data = check.collect(ssh, {"custom_commands": [{
            "name": "invalid", "command": "echo 0", "expected_max": limit,
            "on_fail": "invalid gate"
        }]})
        assert check.validate(data, {})[0] is False
        assert item_results(data)[0]["passed"] is False


def test_dangerous_command_does_not_pass_even_with_numeric_max():
    check = CustomCommandCheck()
    ssh = MagicMock()
    data = check.collect(ssh, {"custom_commands": [{
        "name": "blocked", "command": "rm -rf /tmp/foo",
        "expected_max": 99, "on_fail": "blocked"
    }]})
    ssh.run.assert_not_called()
    assert not check.validate(data, {})[0]
    assert not item_results(data)[0]["passed"]
