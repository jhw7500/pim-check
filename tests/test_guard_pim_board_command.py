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
        "setsid python3 pim_check.py --plan smoke",
        "setsid -f python3 run_comprehensive_verify.py",
        "setsid -cfw python3 pim_check.py --plan smoke",
        "setsid --fork python3 pim_check.py --plan smoke",
        "setsid --wait --ctty python3 run_comprehensive_verify.py",
        "setsid -- python3 pim_check.py --plan smoke",
        "nohup setsid -f python3 pim_check.py --plan smoke",
        "builtin exec python3 pim_check.py --plan smoke",
        "builtin eval 'python3 pim_check.py --plan smoke'",
        "builtin source scripts/test_vflip_frame_compare.sh",
        "builtin . scripts/test_vflip_frame_compare.sh",
        "builtin command python3 pim_check.py --plan smoke",
        "builtin builtin exec python3 run_comprehensive_verify.py",
        "builtin -- exec python3 pim_check.py --plan smoke",
        "nohup builtin exec python3 pim_check.py --plan smoke",
        "sudo python3 pim_check.py --plan smoke",
        "sudo -n python3 run_comprehensive_verify.py",
        "sudo -u root python3 pim_check.py --plan smoke",
        "sudo -uroot python3 run_comprehensive_verify.py",
        "sudo --user=root python3 pim_check.py --plan smoke",
        "sudo -nE -D /tmp python3 pim_check.py --plan smoke",
        "sudo --chdir=/tmp --user root python3 pim_check.py --plan smoke",
        "sudo VAR=value python3 pim_check.py --plan smoke",
        "sudo -- python3 pim_check.py --plan smoke",
        "sudo -i python3 pim_check.py --plan smoke",
        "sudo -s python3 run_comprehensive_verify.py",
        "sudo -k python3 pim_check.py --plan smoke",
        "sudo -h localhost python3 pim_check.py --plan smoke",
        "nohup sudo -n python3 pim_check.py --plan smoke",
        "/tmp/with_pim_board.sh -- python3 pim_check.py --plan smoke",
        "with_pim_board.sh -- python3 pim_check.py --plan smoke",
        "other/scripts/with_pim_board.sh -- python3 run_comprehensive_verify.py",
    ],
)
def test_guard_blocks_direct_board_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "source -- scripts/test_vflip_frame_compare.sh",
        ". -- scripts/test_vflip_frame_compare.sh",
        "builtin source -- scripts/test_vflip_frame_compare.sh",
        "builtin . -- scripts/test_vflip_frame_compare.sh",
    ],
)
def test_guard_blocks_sourced_board_runners_after_option_terminator(
    command: str,
) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "source -- /home/jhw/.config/jhw-control/control.env",
        ". -- /home/jhw/.config/jhw-control/control.env",
    ],
)
def test_guard_allows_safe_source_after_option_terminator(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "bash -c $'python3 pim_check.py --plan smoke'",
        "nohup bash -c $'python3 run_comprehensive_verify.py'",
    ],
)
def test_guard_blocks_board_commands_in_bash_ansi_c_quotes(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        r"bash -c $'python3\x20pim_check.py\x20--plan\x20smoke'",
        r"printf %s $'safe\n'",
    ],
)
def test_guard_fails_closed_on_bash_ansi_c_escape_sequences(
    command: str,
) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "find . -maxdepth 0 -exec python3 pim_check.py --plan smoke \\;",
        "find . -maxdepth 0 -execdir python3 run_comprehensive_verify.py \\;",
        "find . -maxdepth 0 -ok python3 pim_check.py --plan smoke \\;",
        "find . -maxdepth 0 -okdir python3 run_comprehensive_verify.py \\;",
        "find . -maxdepth 0 -exec python3 pim_check.py --plan smoke {} +",
        "find . -maxdepth 0 -exec sh -c "
        "'python3 pim_check.py --plan smoke' \\;",
        "find . -name pim_check.py -exec python3 {} --plan smoke \\;",
        "find . -name pim_check.py -exec {} --plan smoke \\;",
        "find . -name run_comprehensive_verify.py -exec python3 {} \\;",
        "find . -name pim_check.py -exec timeout 30m "
        "python3 ./{} --plan smoke \\;",
        "find . -name pim_check.py -exec sh -c '{} --plan smoke' \\;",
    ],
)
def test_guard_blocks_board_commands_launched_by_find(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


def test_guard_checks_every_find_execution_action() -> None:
    result = _run_guard(
        "find . -maxdepth 0 -exec printf %s safe \\; "
        "-exec python3 pim_check.py --plan smoke \\;"
    )

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "watch -x python3 pim_check.py --plan smoke",
        "watch python3 pim_check.py --plan smoke",
        "watch 'python3 pim_check.py --plan smoke'",
        "watch --interval=5 --exec python3 run_comprehensive_verify.py",
        "watch -bpxn5 python3 run_comprehensive_verify.py",
        "watch -x -- python3 pim_check.py --plan=smoke",
        "watch -x timeout 30m python3 pim_check.py --plan smoke",
        "watch --differences=permanent python3 pim_check.py --plan smoke",
    ],
)
def test_guard_blocks_board_commands_launched_by_watch(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "taskset 0x1 python3 pim_check.py --plan smoke",
        "taskset -c 0 python3 run_comprehensive_verify.py",
        "taskset --cpu-list 0 python3 pim_check.py --plan=smoke",
        "taskset -ac 0 python3 run_comprehensive_verify.py",
        "taskset -- 0x1 python3 pim_check.py --plan smoke",
        "taskset 0x1 scripts/test_vflip_frame_compare.sh",
        "timeout 30m taskset 0x1 python3 pim_check.py --plan smoke",
    ],
)
def test_guard_blocks_board_commands_launched_by_taskset(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "chrt --other 0 python3 pim_check.py --plan smoke",
        "chrt -o 0 python3 run_comprehensive_verify.py",
        "chrt --fifo 1 scripts/test_vflip_frame_compare.sh",
        "chrt -rR 1 python3 pim_check.py --plan=smoke",
        "chrt -dT1000000 -P 1000000 -D1000000 0 "
        "python3 run_comprehensive_verify.py",
        "chrt --deadline --sched-runtime=1000000 --sched-period 1000000 "
        "--sched-deadline=1000000 0 python3 pim_check.py --plan smoke",
        "chrt -- 0 python3 pim_check.py --plan smoke",
        "timeout 30m chrt --batch 0 python3 pim_check.py --plan smoke",
    ],
)
def test_guard_blocks_board_commands_launched_by_chrt(command: str) -> None:
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
        "setsid pytest -q tests/test_plan_load.py",
        "setsid -f printf %s safe",
        "setsid -h",
        "setsid -V",
        "setsid --help",
        "setsid --version",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "setsid python3 pim_check.py --plan smoke",
        "builtin printf %s safe",
        "builtin command -v python3 pim_check.py --plan smoke",
        "builtin",
        "builtin --",
        "builtin -- printf %s safe",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "builtin exec python3 pim_check.py --plan smoke",
        "sudo pytest -q tests/test_plan_load.py",
        "sudo -n printf %s safe",
        "sudo -C3 -uroot printf %s safe",
        "sudo --preserve-env=PATH printf %s safe",
        "sudo --help",
        "sudo -h",
        "sudo --version",
        "sudo -V",
        "sudo -l python3 pim_check.py --plan smoke",
        "sudo --list python3 run_comprehensive_verify.py",
        "sudo -v",
        "sudo -K",
        "sudo -k",
        "./scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "python3 pim_check.py --plan smoke",
        f"{ROOT / 'scripts' / 'with_pim_board.sh'} --for 30m "
        "--purpose safe -- python3 pim_check.py --plan smoke",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "sudo python3 pim_check.py --plan smoke",
    ],
)
def test_guard_allows_wrapped_or_unrelated_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "bash -c $'pytest -q tests/test_plan_load.py'",
        "printf %s \"$'python3 pim_check.py --plan smoke'\"",
        r"printf %s \$'python3 pim_check.py --plan smoke'",
    ],
)
def test_guard_allows_safe_or_literal_bash_ansi_c_quotes(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "find . -maxdepth 0 -print",
        "find . -maxdepth 0 -exec printf %s {} \\;",
        "find . -maxdepth 0 -execdir pytest -q tests/test_plan_load.py {} +",
        "find . -maxdepth 0 -exec scripts/with_pim_board.sh --for 30m "
        "--purpose safe -- python3 pim_check.py --plan smoke {} \\;",
    ],
)
def test_guard_allows_safe_find_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "watch -x printf %s safe",
        "watch -n 5 pytest -q tests/test_plan_load.py",
        "watch --differences=permanent date",
        "watch --help",
        "watch --version",
        "watch -x scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "python3 pim_check.py --plan smoke",
        "watch scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "python3 pim_check.py --plan smoke",
    ],
)
def test_guard_allows_safe_watch_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "taskset 0x1 printf %s safe",
        "taskset -c 0 pytest -q tests/test_plan_load.py",
        "taskset -p 123",
        "taskset -p 0x1 123",
        "taskset -pc 0 123",
        "taskset --pid --cpu-list 0 123",
        "taskset --help",
        "taskset --version",
        "taskset -h",
        "taskset -V",
        "taskset 0x1 scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "python3 pim_check.py --plan smoke",
    ],
)
def test_guard_allows_safe_taskset_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "chrt --other 0 printf %s safe",
        "chrt -o 0 pytest -q tests/test_plan_load.py",
        "chrt -p 123",
        "chrt -p 0 123",
        "chrt --pid 0 123",
        "chrt -ap 123",
        "chrt -vap 0 123",
        "chrt -m",
        "chrt --max",
        "chrt --help",
        "chrt --version",
        "chrt -h",
        "chrt -V",
        "chrt 0 scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "python3 pim_check.py --plan smoke",
    ],
)
def test_guard_allows_safe_chrt_commands(command: str) -> None:
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
        "setsid",
        "setsid -f",
        "setsid --",
        "setsid -x true",
        "setsid --unknown true",
        "builtin -x printf %s safe",
        "builtin --help",
        "sudo",
        "sudo -n",
        "sudo -s",
        "sudo -i",
        "sudo -u",
        "sudo --user=",
        "sudo -C",
        "sudo -x true",
        "sudo --unknown true",
        "sudo -e run_comprehensive_verify.py",
        "bash -c $'python3 pim_check.py --plan smoke",
    ],
)
def test_guard_fails_closed_on_malformed_launchers(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "find . -exec",
        "find . -exec \\;",
        "find . -exec python3 pim_check.py --plan smoke",
        "find . -okdir printf %s safe",
    ],
)
def test_guard_fails_closed_on_malformed_find_execution_actions(
    command: str,
) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "watch",
        "watch -x",
        "watch -n",
        "watch --interval",
        "watch --interval=",
        "watch -z date",
        "watch --unknown date",
        "watch --exec=value date",
    ],
)
def test_guard_fails_closed_on_malformed_watch_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "taskset",
        "taskset --",
        "taskset 0x1",
        "taskset -c",
        "taskset --cpu-list",
        "taskset -p",
        "taskset --pid --",
        "taskset -p 0x1 123 extra",
        "taskset -x 0x1 true",
        "taskset --unknown 0x1 true",
    ],
)
def test_guard_fails_closed_on_malformed_taskset_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "chrt",
        "chrt --",
        "chrt 0",
        "chrt -T",
        "chrt --sched-runtime",
        "chrt --sched-runtime=",
        "chrt -p",
        "chrt --pid --",
        "chrt -p 0 123 extra",
        "chrt -x 0 true",
        "chrt --unknown 0 true",
    ],
)
def test_guard_fails_closed_on_malformed_chrt_commands(command: str) -> None:
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
