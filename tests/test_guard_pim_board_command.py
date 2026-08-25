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
        "python3 -mpim_check --plan smoke",
        "pim-check --plan smoke",
        "python3 run_mixed_combo_verify.py",
        "python3 run_comprehensive_verify.py",
        "python3 run_bps_quick.py",
        "python3 run_smart_verify.py",
        "python3 run_channel_verify.py",
        "python3 run_failed_retry.py",
        "echo precheck && python3 pim_check.py --plan nightly",
        "echo precheck\npython3 pim_check.py --plan nightly",
        "env -i python3 pim_check.py --plan smoke",
        "env -- python3 pim_check.py --plan smoke",
        'env -S "python3 pim_check.py --plan smoke"',
        "env -S'python3 pim_check.py --plan smoke'",
        "command python3 pim_check.py --plan smoke",
        "python3 -W ignore pim_check.py --plan smoke",
        "timeout 30m python3 pim_check.py --plan smoke",
        "nohup python3 run_comprehensive_verify.py",
        'bash -c "python3 pim_check.py --plan smoke"',
        'timeout --kill-after=10s 30m nohup sh -c "python3 pim_check.py --plan smoke"',
        "(python3 pim_check.py --plan smoke)",
        "{ python3 run_comprehensive_verify.py; }",
        "if true; then python3 pim_check.py --plan smoke; fi",
        "exec python3 pim_check.py --plan smoke",
        "exec -- python3 pim_check.py --plan smoke",
        "exec -cl -a board-runner python3 run_comprehensive_verify.py",
        "env -iS'python3 pim_check.py --plan smoke'",
        "env -ivS'python3 pim_check.py --plan smoke'",
        "env - python3 pim_check.py --plan smoke",
        'echo "$(python3 pim_check.py --plan smoke)"',
        "echo `python3 pim_check.py --plan smoke`",
        "scripts/with_pim_board.sh --for 30m --purpose x -- "
        'echo "$(python3 pim_check.py --plan smoke)"',
        "./scripts/test_vflip_frame_compare.sh",
        "bash scripts/test_vflip_frame_compare.sh",
        "eval 'python3 pim_check.py --plan smoke'",
        "eval python3 pim_check.py --plan smoke",
        "source scripts/test_vflip_frame_compare.sh",
        ". scripts/test_vflip_frame_compare.sh",
        "nice python3 pim_check.py --plan smoke",
        "nice -n 5 python3 pim_check.py --plan smoke",
        "nice -n5 python3 pim_check.py --plan smoke",
        "nice --adjustment=5 python3 pim_check.py --plan smoke",
        "nice -10 python3 pim_check.py --plan smoke",
        "stdbuf -oL python3 pim_check.py --plan smoke",
        "stdbuf -o L python3 pim_check.py --plan smoke",
        "stdbuf --output=L python3 pim_check.py --plan smoke",
        "stdbuf --output L python3 pim_check.py --plan smoke",
        "stdbuf -i0 -oL -e0 python3 run_comprehensive_verify.py",
        "stdbuf -oL -- python3 pim_check.py --plan smoke",
        "nice -n 5 stdbuf -oL python3 pim_check.py --plan smoke",
        "xargs python3 pim_check.py --plan smoke </dev/null",
        "xargs -0r python3 pim_check.py --plan smoke",
        "xargs -a /dev/null python3 run_comprehensive_verify.py",
        "xargs -n1 python3 pim_check.py --plan smoke",
        "xargs --max-args=1 python3 pim_check.py --plan smoke",
        "xargs --max-args 1 python3 pim_check.py --plan smoke",
        "xargs -iITEM python3 -m pim_check --plan smoke",
        "xargs -ri python3 -m pim_check --plan smoke",
        "xargs --replace=ITEM python3 -m pim_check --plan smoke",
        "xargs --replace python3 -m pim_check --plan smoke",
        "xargs --show-limits python3 pim_check.py --plan smoke",
        "xargs -- python3 pim_check.py --plan smoke",
        "xargs -I{} python3 -m pim_check --plan smoke",
        "stdbuf -oL xargs -n1 python3 pim_check.py --plan smoke",
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
        'env -S "printf %s safe"',
        "env -S'printf %s safe'",
        'python3 -W ignore -c "print(\"safe\")"',
        "python3 -mjson.tool --help",
        "command -v python3 pim_check.py --plan smoke",
        "rg run_comprehensive_verify.py",
        "pytest -q tests/test_plan_load.py",
        "timeout 30m scripts/with_pim_board.sh --for 30m --purpose safe -- true",
        'bash -c "pytest -q tests/test_plan_load.py"',
        "bash --help",
        "bash --version",
        "sh --help",
        "nohup rg run_comprehensive_verify.py README.md",
        "(scripts/with_pim_board.sh --for 30m --purpose safe -- true)",
        "{ pytest -q tests/test_plan_load.py; }",
        "if true; then pytest -q tests/test_plan_load.py; fi",
        "exec scripts/with_pim_board.sh --for 30m --purpose safe -- true",
        "exec -cl -a safe-run pytest -q tests/test_plan_load.py",
        "env -iS'printf %s safe'",
        "env - printf %s safe",
        'echo "$(date +%s)"',
        "echo `date +%s`",
        "echo '$(python3 pim_check.py --plan smoke)'",
        r'echo "\$(python3 pim_check.py --plan smoke)"',
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "bash -c 'echo \"$(python3 pim_check.py --plan smoke)\"'",
        "eval 'printf %s safe'",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "eval 'python3 pim_check.py --plan smoke'",
        "source /home/jhw/.config/jhw-control/control.env",
        ". /home/jhw/.config/jhw-control/control.env",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "source scripts/test_vflip_frame_compare.sh",
        "nice pytest -q tests/test_plan_load.py",
        "nice --help",
        "stdbuf -oL pytest -q tests/test_plan_load.py",
        "stdbuf --help",
        "stdbuf --version",
        "xargs printf %s </dev/null",
        "xargs -r printf %s",
        "xargs",
        "xargs -r",
        "xargs --help",
        "xargs --version",
        "xargs -a run_comprehensive_verify.py printf %s",
        "xargs -arun_comprehensive_verify.py printf %s",
        "xargs --arg-file run_comprehensive_verify.py printf %s",
        "xargs -I{} printf %s",
        "xargs --replace={} printf %s",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "nice python3 pim_check.py --plan smoke",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "stdbuf -oL python3 pim_check.py --plan smoke",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "xargs python3 pim_check.py --plan smoke",
    ],
)
def test_guard_allows_wrapped_or_unrelated_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "bash -s <<< 'python3 pim_check.py --plan smoke'",
        "bash <<< 'python3 pim_check.py --plan smoke'",
        "printf 'python3 pim_check.py --plan smoke\\n' | bash",
        "printf 'printf safe\\n' | bash",
        "bash",
        "bash -s",
        "bash -",
        "bash --",
        "bash </dev/null",
        "bash 0<<<'printf safe'",
        "sh",
        "dash -s",
        "zsh -s",
        "bash /dev/stdin <<< 'python3 pim_check.py --plan smoke'",
        "sh /dev/fd/0 <<< './scripts/test_vflip_frame_compare.sh'",
        "dash /proc/self/fd/0 <<< 'python3 -m pim_check --plan smoke'",
    ],
)
def test_guard_fails_closed_on_shell_commands_read_from_stdin(
    command: str,
) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


def test_guard_does_not_let_wrapper_in_one_segment_cover_a_later_direct_run() -> None:
    result = _run_guard(
        "scripts/with_pim_board.sh --for 30m --purpose safe -- true; "
        "python3 pim_check.py --plan smoke"
    )

    assert result.returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "timeout 30m",
        "nohup",
        "bash -c",
        "exec -a",
        "exec -x true",
        "env -iS",
        "env -iX true",
        "nice -n",
        "nice --adjustment=",
        "nice -x true",
        "stdbuf",
        "stdbuf true",
        "stdbuf -o",
        "stdbuf --output=",
        "stdbuf -x true",
        "stdbuf -oL",
        "xargs -a",
        "xargs --arg-file",
        "xargs --max-args=",
        "xargs -z true",
        "xargs -0z true",
        "xargs --unknown true",
    ],
)
def test_guard_fails_closed_on_malformed_launchers(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "for item in one; do printf %s \'$item\'; done",
        "case value in value) printf %s safe;; esac",
        "coproc printf %s safe",
        "time pytest -q tests/test_plan_load.py",
    ],
)
def test_guard_fails_closed_on_unsupported_compound_syntax(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


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
    assert "scripts/with_pim_board.sh" in result.stderr
    assert "--for/--until" in result.stderr


def test_project_settings_register_bash_pretooluse_guard() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]

    assert len(entries) == 1
    assert entries[0]["matcher"] == "Bash"
    hook = entries[0]["hooks"][0]
    assert hook["type"] == "command"
    assert "$CLAUDE_PROJECT_DIR/scripts/guard_pim_board_command.py" in hook["command"]


def test_guard_is_directly_executable() -> None:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}})

    result = subprocess.run(
        [str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
