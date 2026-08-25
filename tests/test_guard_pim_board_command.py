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
        "unshare --fork python3 pim_check.py --plan smoke",
        "unshare -fp python3 run_comprehensive_verify.py",
        "unshare --root /tmp -- python3 -m pim_check --plan smoke",
        "timeout 30m unshare --user python3 pim_check.py --plan smoke",
        "flock /tmp/pim.lock python3 pim_check.py --plan smoke",
        "flock -n /tmp/pim.lock python3 run_comprehensive_verify.py",
        "flock /tmp/pim.lock -c 'python3 pim_check.py --plan smoke'",
        "flock /tmp/pim.lock -c'python3 pim_check.py --plan smoke'",
        "flock --command='python3 -m pim_check --plan smoke' /tmp/pim.lock",
        "flock /tmp/pim.lock -n python3 pim_check.py --plan smoke",
        "flock -xnE4 /tmp/pim.lock python3 pim_check.py --plan smoke",
        "flock -w.5 /tmp/pim.lock python3 pim_check.py --plan smoke",
        "flock --timeout=.5 /tmp/pim.lock python3 pim_check.py --plan smoke",
        "flock -w 1 -- /tmp/pim.lock timeout 30m python3 pim_check.py --plan smoke",
        "flock 9 python3 pim_check.py --plan smoke",
        "setarch linux64 python3 pim_check.py --plan smoke",
        "setarch linux64 -vR python3 run_comprehensive_verify.py",
        "setarch --addr-no-randomize python3 pim_check.py --plan smoke",
        "setarch -R linux64 python3 pim_check.py --plan smoke",
        "linux32 python3 pim_check.py --plan smoke",
        "linux64 -R python3 run_comprehensive_verify.py",
        "i386 python3 -m pim_check --plan smoke",
        "x86_64 python3 pim_check.py --plan smoke",
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
        ">/tmp/plan.log python3 pim_check.py --plan smoke",
        "> /tmp/plan.log python3 pim_check.py --plan smoke",
        "2>/tmp/err python3 run_comprehensive_verify.py",
        "2> /tmp/err python3 run_comprehensive_verify.py",
        "{fd}>/tmp/log scripts/test_vflip_frame_compare.sh",
        ">>/tmp/plan.log python3 pim_check.py --plan=smoke",
        ">|/tmp/plan.log python3 pim_check.py --plan smoke",
        "</tmp/input python3 pim_check.py --plan smoke",
        "< /tmp/input python3 run_comprehensive_verify.py",
        "<>/tmp/io python3 pim_check.py --plan smoke",
        "<<<payload python3 pim_check.py --plan smoke",
        "<<EOF python3 pim_check.py --plan smoke",
        ">out 2>err python3 pim_check.py --plan smoke",
        "FOO=1 >out BAR=2 python3 pim_check.py --plan smoke",
        "env >out FOO=1 python3 pim_check.py --plan smoke",
        "bash -c '>/tmp/plan.log python3 pim_check.py --plan smoke'",
        "timeout 30m bash -c "
        "'2>/tmp/err python3 run_comprehensive_verify.py'",
        "cd /tmp && >/tmp/log scripts/with_pim_board.sh --for 30m "
        "--purpose unsafe -- python3 pim_check.py --plan smoke",
    ],
)
def test_guard_blocks_board_commands_after_leading_redirections(
    command: str,
) -> None:
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
        "bash -c 'exec \"$@\"' _ python3 pim_check.py --plan smoke",
        "bash -c '$0 --plan smoke' pim-check",
        "sh -c 'python3 \"$1\" --plan smoke' _ pim_check.py",
        "dash -c 'exec \"$*\"' _ python3 run_comprehensive_verify.py",
        "zsh -c 'exec \"${@}\"' _ scripts/test_vflip_frame_compare.sh",
        "bash -ec 'exec \"$@\"' _ python3 pim_check.py --plan smoke",
        "timeout 30m bash -c 'exec \"$@\"' _ python3 pim_check.py --plan smoke",
        "bash -c 'printf %s safe' ignored",
        "bash -c 'exec \"$@\"' _ scripts/with_pim_board.sh --for 30m "
        "--purpose unsafe -- python3 pim_check.py --plan smoke",
    ],
)
def test_guard_fails_closed_on_shell_c_positional_operands(
    command: str,
) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        'runner=pim_check.py; python3 "$runner" --plan smoke',
        'runner=pim-check; "$runner" --plan smoke',
        "script=run_comprehensive_verify.py; timeout 30m "
        'python3 "${script}"',
        'module=pim_check; python3 -m "$module" --plan smoke',
        "script=scripts/test_vflip_frame_compare.sh; "
        'bash "$script"',
        "script=scripts/test_vflip_frame_compare.sh; "
        'source "$script"',
        "python3 `printf pim_check.py` --plan smoke",
        "python3 $(printf pim_check.py) --plan smoke",
        "python3 pim_check.* --plan smoke",
        "python3 pim_{check,other}.py --plan smoke",
    ],
)
def test_guard_fails_closed_on_dynamic_execution_targets(
    command: str,
) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        'flag=--plan; python3 pim_check.py "$flag" smoke',
        "flags='--plan smoke'; python3 pim_check.py $flags",
        'flag=--plan; python3 -m pim_check "$flag" smoke',
        'flag=--plan; pim-check "$flag" smoke',
        "python3 pim_check.py --{pl,other}an smoke",
        'case=smoke; python3 pim_check.py --case "$case"',
    ],
)
def test_guard_fails_closed_on_dynamic_pim_check_arguments(
    command: str,
) -> None:
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
        "ionice -c 3 python3 pim_check.py --plan smoke",
        "ionice -c3 python3 run_comprehensive_verify.py",
        "ionice -t -c 2 -n 0 scripts/test_vflip_frame_compare.sh",
        "ionice --class=idle python3 pim_check.py --plan=smoke",
        "ionice --class best-effort --classdata=0 "
        "python3 run_comprehensive_verify.py",
        "ionice -- python3 pim_check.py --plan smoke",
        "timeout 30m ionice -c 3 python3 pim_check.py --plan smoke",
    ],
)
def test_guard_blocks_board_commands_launched_by_ionice(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "script -q -c 'python3 pim_check.py --plan smoke' /dev/null",
        "script -q -c'python3 pim_check.py --plan smoke' /dev/null",
        "script --quiet --command 'python3 run_comprehensive_verify.py' "
        "/dev/null",
        "script --command='python3 pim_check.py --plan=smoke' /dev/null",
        "script -qec 'scripts/test_vflip_frame_compare.sh' /dev/null",
        "script -aefq -E auto -B /tmp/io -I /tmp/in -O /tmp/out "
        "-T /tmp/time -m advanced -o 1M -c "
        "'python3 pim_check.py --plan smoke' /dev/null",
        "script --append --return --flush --force --quiet --echo=auto "
        "--log-io=/tmp/io --log-in /tmp/in --log-out=/tmp/out "
        "--log-timing /tmp/time --logging-format=advanced "
        "--output-limit 1M --command "
        "'python3 run_comprehensive_verify.py' /dev/null",
        "script -qt/tmp/time -c 'python3 pim_check.py --plan smoke' "
        "/dev/null",
        "script --timing=/tmp/time --command "
        "'python3 pim_check.py --plan smoke' /dev/null",
        "script -q -c 'timeout 30m python3 pim_check.py --plan smoke' "
        "/dev/null",
        "timeout 30m script -q -c "
        "'python3 pim_check.py --plan smoke' /dev/null",
        "script -q -c 'bash -c \"python3 pim_check.py --plan smoke\"' "
        "/dev/null",
        "cd /tmp && script -q -c "
        "'scripts/with_pim_board.sh --for 30m --purpose unsafe -- "
        "python3 pim_check.py --plan smoke' /dev/null",
    ],
)
def test_guard_blocks_board_commands_launched_by_script(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "prlimit -- python3 pim_check.py --plan smoke",
        "prlimit python3 pim_check.py --plan smoke",
        "prlimit --nofile=1024:2048 python3 run_comprehensive_verify.py",
        "prlimit --nofile python3 pim_check.py --plan=smoke",
        "prlimit -n1024:2048 scripts/test_vflip_frame_compare.sh",
        "prlimit -n scripts/test_vflip_frame_compare.sh",
        "prlimit --core=:unlimited --cpu=10 -- "
        "python3 pim_check.py --plan smoke",
        "prlimit --noheadings --raw --verbose --output RESOURCE,SOFT -- "
        "python3 run_comprehensive_verify.py",
        "prlimit --noheadings --raw --verbose -oRESOURCE,SOFT "
        "python3 pim_check.py --plan smoke",
        "timeout 30m prlimit -- python3 pim_check.py --plan smoke",
        "prlimit -- bash -c 'python3 pim_check.py --plan smoke'",
        "cd /tmp && prlimit -- scripts/with_pim_board.sh --for 30m "
        "--purpose unsafe -- python3 pim_check.py --plan smoke",
        "prlimit --core --data --nice --fsize --sigpending --memlock "
        "--rss --nofile --msgqueue --rtprio --stack --cpu --nproc "
        "--as --locks --rttime -- python3 pim_check.py --plan smoke",
    ],
)
def test_guard_blocks_board_commands_launched_by_prlimit(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "script",
        "script -q /dev/null",
        "script --quiet -- /dev/null",
        "printf 'python3 pim_check.py --plan smoke\\n' | script -q /dev/null",
        "script -q /dev/null <<< 'python3 pim_check.py --plan smoke'",
    ],
)
def test_guard_fails_closed_on_interactive_script_commands(
    command: str,
) -> None:
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
        "unshare --fork printf %s safe",
        "unshare -Ur true",
        "unshare --root=/tmp -- pytest -q tests/test_plan_load.py",
        "unshare --help",
        "unshare --version",
        "flock /tmp/pim.lock printf %s safe",
        "flock -n 9",
        "flock /tmp/pim.lock -c 'printf %s safe'",
        "flock --command='printf %s safe' /tmp/pim.lock",
        "flock /tmp/pim.lock scripts/with_pim_board.sh --for 30m "
        "--purpose safe -- python3 pim_check.py --plan smoke",
        "flock --help",
        "flock --version",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "setsid python3 pim_check.py --plan smoke",
        "setarch linux64 printf %s safe",
        "setarch -R pytest -q tests/test_plan_load.py",
        "linux32 printf %s safe",
        "linux64 -vR pytest -q tests/test_plan_load.py",
        "i386 true",
        "x86_64 --addr-no-randomize printf %s safe",
        "setarch --list",
        "setarch --help",
        "setarch --version",
        "linux64 -h",
        "linux64 -V",
        "arch",
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "setarch linux64 python3 pim_check.py --plan smoke",
        "setarch linux64 scripts/with_pim_board.sh --for 30m "
        "--purpose safe -- python3 pim_check.py --plan smoke",
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
        ">/tmp/safe.log printf %s safe",
        "> /tmp/safe.log pytest -q tests/test_plan_load.py",
        ">>/tmp/safe.log printf %s safe",
        ">|/tmp/safe.log printf %s safe",
        "</tmp/input printf %s safe",
        "<>/tmp/io printf %s safe",
        "<<EOF printf %s safe",
        "<<-EOF printf %s safe",
        "<<<payload printf %s safe",
        "2>run_comprehensive_verify.py printf %s safe",
        "2> run_comprehensive_verify.py printf %s safe",
        ">out <in printf %s safe",
        "FOO=1 >out BAR=2 printf %s safe",
        "env >out FOO=1 printf %s safe",
        "env -i >out FOO=1 printf %s safe",
        ">/tmp/log scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "python3 pim_check.py --plan smoke",
        "bash -c '>/tmp/log printf %s safe'",
        ">/tmp/log",
        "> /tmp/log",
        f"cd /tmp && >/tmp/log {ROOT / 'scripts' / 'with_pim_board.sh'} "
        "--for 30m --purpose safe -- python3 pim_check.py --plan smoke",
    ],
)
def test_guard_allows_safe_commands_after_leading_redirections(
    command: str,
) -> None:
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
        'echo "$HOME"',
        "printf '%s\\n' '*.py'",
        'python3 helper.py "$HOME" "*.yaml"',
        'python3 -c \'print("$HOME")\'',
        "scripts/with_pim_board.sh --for 30m --purpose safe -- "
        'python3 "$runner" "$flag"',
    ],
)
def test_guard_allows_expansions_in_unrelated_data_arguments(
    command: str,
) -> None:
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
        "ionice -c 3 printf %s safe",
        "ionice -c3 pytest -q tests/test_plan_load.py",
        "ionice",
        "ionice -p 123",
        "ionice -p123 456",
        "ionice --pid 123",
        "ionice -P 456",
        "ionice --pgid=456",
        "ionice -u 1000",
        "ionice --uid=1000",
        "ionice --help",
        "ionice --version",
        "ionice -h",
        "ionice -V",
        "ionice -c 3 scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "python3 pim_check.py --plan smoke",
    ],
)
def test_guard_allows_safe_ionice_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "script -q -c 'printf %s safe' /dev/null",
        "script --command 'pytest -q tests/test_plan_load.py' /dev/null",
        "script --command='printf %s safe' /dev/null",
        "script -q -c 'printf %s safe' run_comprehensive_verify.py",
        "script -aefq -E never -B /tmp/io -I /tmp/in -O /tmp/out "
        "-T /tmp/time -m classic -o 1M -c 'printf %s safe' /dev/null",
        "script -qt/tmp/time -c 'printf %s safe' /dev/null",
        "script --timing=/tmp/time --command 'printf %s safe' /dev/null",
        "script -t -c 'printf %s safe' /dev/null",
        "script --timing -c 'printf %s safe' /dev/null",
        "script -q -c 'scripts/with_pim_board.sh --for 30m "
        "--purpose safe -- python3 pim_check.py --plan smoke' /dev/null",
        f"cd /tmp && script -q -c '{ROOT / 'scripts' / 'with_pim_board.sh'} "
        "--for 30m --purpose safe -- python3 pim_check.py --plan smoke' "
        "/dev/null",
        "script -c 'printf %s safe' -- /dev/null",
        "script --help",
        "script --version",
        "script -h",
        "script -V",
    ],
)
def test_guard_allows_safe_script_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "prlimit",
        "prlimit --",
        "prlimit --nofile",
        "prlimit --nofile=1024:2048",
        "prlimit -n1024:2048",
        "prlimit --pid 123",
        "prlimit --pid=123 --rss --nofile=1024:4095",
        "prlimit -p123 -m -n1024:4095",
        "prlimit --noheadings --raw --verbose --output RESOURCE,SOFT "
        "--pid 123",
        "prlimit --noheadings --raw --verbose -oRESOURCE,SOFT -p123",
        "prlimit printf %s safe",
        "prlimit -- pytest -q tests/test_plan_load.py",
        "prlimit -c -d -e -f -i -l -m -n -q -r -s -t -u -v -x -y "
        "-- printf %s safe",
        "prlimit --core --data --nice --fsize --sigpending --memlock "
        "--rss --nofile --msgqueue --rtprio --stack --cpu --nproc "
        "--as --locks --rttime -- printf %s safe",
        "prlimit --output run_comprehensive_verify.py -- printf %s safe",
        "prlimit -- scripts/with_pim_board.sh --for 30m --purpose safe -- "
        "python3 pim_check.py --plan smoke",
        f"cd /tmp && prlimit -- {ROOT / 'scripts' / 'with_pim_board.sh'} "
        "--for 30m --purpose safe -- python3 pim_check.py --plan smoke",
        "prlimit --help",
        "prlimit --version",
        "prlimit -h",
        "prlimit -V",
    ],
)
def test_guard_allows_safe_prlimit_commands(command: str) -> None:
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


def test_guard_blocks_relative_wrapper_after_directory_change() -> None:
    result = _run_guard(
        "cd /tmp && scripts/with_pim_board.sh --for 30m --purpose x -- "
        "python3 pim_check.py --plan smoke"
    )

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


def test_guard_allows_absolute_wrapper_after_directory_change() -> None:
    result = _run_guard(
        f"cd /tmp && {ROOT / 'scripts' / 'with_pim_board.sh'} "
        "--for 30m --purpose x -- python3 pim_check.py --plan smoke"
    )

    assert result.returncode == 0
    assert result.stderr == ""


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
        "unshare",
        "unshare --fork",
        "unshare --",
        "unshare --root",
        "unshare --root=",
        "unshare -x true",
        "unshare --unknown true",
        "flock",
        "flock --",
        "flock /tmp/pim.lock",
        "flock -n",
        "flock -E",
        "flock --conflict-exit-code= /tmp/pim.lock true",
        "flock -w",
        "flock --timeout= /tmp/pim.lock true",
        "flock /tmp/pim.lock -c",
        "flock /tmp/pim.lock -c ''",
        "flock /tmp/pim.lock -c 'printf %s safe' extra",
        "flock -Z /tmp/pim.lock true",
        "flock --unknown /tmp/pim.lock true",
        "setarch",
        "setarch --",
        "setarch linux64",
        "setarch linux64 --",
        "setarch -Q true",
        "setarch --unknown true",
        "setarch --addr-no-randomize=true",
        "linux64",
        "linux64 --",
        "linux64 -Q true",
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
        ">",
        "2>",
        "{fd}>",
        "<",
        ">>",
        ">|",
        "<>",
        "<<",
        "<<<",
        "2>&1 python3 pim_check.py --plan smoke",
        "2>&1 printf %s safe",
    ],
)
def test_guard_fails_closed_on_ambiguous_or_incomplete_redirections(
    command: str,
) -> None:
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
        "ionice -c",
        "ionice -n",
        "ionice --class",
        "ionice --class=",
        "ionice --classdata",
        "ionice --classdata=",
        "ionice -p",
        "ionice -P",
        "ionice -u",
        "ionice --pid=",
        "ionice --pgid=",
        "ionice --uid=",
        "ionice -x true",
        "ionice --unknown true",
    ],
)
def test_guard_fails_closed_on_malformed_ionice_commands(command: str) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "script -c",
        "script -c '' /dev/null",
        "script --command",
        "script --command= /dev/null",
        "script -E",
        "script --echo",
        "script --echo=",
        "script -B",
        "script --log-io=",
        "script -I",
        "script --log-in=",
        "script -O",
        "script --log-out=",
        "script -T",
        "script --log-timing=",
        "script -m",
        "script --logging-format=",
        "script -o",
        "script --output-limit=",
        "script -x -c 'printf %s safe' /dev/null",
        "script --unknown -c 'printf %s safe' /dev/null",
        "script -c 'printf %s safe' /tmp/one /tmp/two",
        "script -c 'printf %s safe' --command 'printf %s safe' /dev/null",
    ],
)
def test_guard_fails_closed_on_malformed_script_commands(
    command: str,
) -> None:
    result = _run_guard(command)

    assert result.returncode == 2
    assert "scripts/with_pim_board.sh" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "prlimit -p",
        "prlimit --pid",
        "prlimit --pid=",
        "prlimit --pid --",
        "prlimit -o",
        "prlimit --output",
        "prlimit --output=",
        "prlimit --output --pid 123",
        "prlimit -z true",
        "prlimit --unknown true",
        "prlimit --pid 123 printf %s safe",
        "prlimit -p123 printf %s safe",
        "prlimit --pid 123 -- printf %s safe",
        "prlimit --pid 123 --pid 456",
        "prlimit --nofile=",
    ],
)
def test_guard_fails_closed_on_malformed_prlimit_commands(
    command: str,
) -> None:
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
