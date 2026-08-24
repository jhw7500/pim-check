"""Claude Bash guard for issue #108's direct PIM board commands."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "guard_pim_board_command.py"
SETTINGS = ROOT / ".claude" / "settings.json"


def _run_guard(command: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "command",
    [
        "python3 pim_check.py --plan smoke --host 192.168.214.4",
        "python3 -m pim_check --plan smoke",
        "pim-check --plan smoke",
        "python3 run_mixed_combo_verify.py",
        "python3 run_comprehensive_verify.py",
        "python3 run_bps_quick.py",
        "echo precheck && python3 pim_check.py --plan nightly",
        "echo precheck\npython3 pim_check.py --plan nightly",
        "env -i python3 pim_check.py --plan smoke",
        "env -- python3 pim_check.py --plan smoke",
        "command python3 pim_check.py --plan smoke",
    ],
)
def test_guard_blocks_direct_board_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "scripts/with_pim_board.sh --for 30m --purpose manual -- python3 pim_check.py --plan smoke",
        "bash scripts/auto_overnight.sh",
        "./scripts/auto_weekend.sh",
        "python3 pim_check.py --list-plans",
        "command -v python3 pim_check.py --plan smoke",
        "rg run_comprehensive_verify.py",
        "pytest -q tests/test_plan_load.py",
    ],
)
def test_guard_allows_wrapped_or_unrelated_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


def test_guard_does_not_let_wrapper_in_one_segment_cover_a_later_direct_run() -> None:
    result = _run_guard(
        "scripts/with_pim_board.sh --for 30m --purpose safe -- true; "
        "python3 pim_check.py --plan smoke"
    )

    assert result.returncode == 2


def test_guard_fails_closed_on_malformed_hook_json() -> None:
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input="not-json",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid hook input" in result.stderr.lower()


def test_project_settings_register_bash_pretooluse_guard() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]

    assert len(entries) == 1
    assert entries[0]["matcher"] == "Bash"
    hook = entries[0]["hooks"][0]
    assert hook["type"] == "command"
    assert "$CLAUDE_PROJECT_DIR/scripts/guard_pim_board_command.py" in hook["command"]
