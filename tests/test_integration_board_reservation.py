"""Cross-entry-point contract for the shared PIM board reservation."""
from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest
import yaml

from setup import DEFAULT_REBOOT_TIMEOUT


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "with_pim_board.sh"
DEADLINE_SUPERVISOR = ROOT / "scripts" / "run_with_deadline.py"
MIN_AUTOMATION_TERM_GRACE_SECONDS = max(
    30 * 60,
    2 * DEFAULT_REBOOT_TIMEOUT + 5 * 60,
)
MIN_LEASE_RELEASE_MARGIN_SECONDS = 60

FAKE_CONTROL = """#!/usr/bin/env bash
set -u
if [[ "${1:-}" == "board" && "${2:-}" == "status" ]]; then
    [[ -r "$FAKE_JHW_ACTIVE" ]] || exit 3
    IFS= read -r active_session < "$FAKE_JHW_ACTIVE"
    active_purpose="${FAKE_STATUS_PURPOSE:-test}"
    granted_until="${FAKE_GRANTED_UNTIL:-2026-08-26T00:00:00.000Z}"
    printf '{"command":"board status","result":{"boards":[{"board_id":"%s","holders":[{"holder_id":"hold-test","session":"%s","mode":"exclusive","purpose":"%s","acquired_at":"2026-08-25T00:00:00.000Z","granted_until":"%s","liveness":"alive","expired":false,"overstay":false,"extended_after_expiry":false}],"reservations":[]}]}}\\n' "$3" "$active_session" "$active_purpose" "$granted_until"
    exit 0
fi
if [[ "${FAKE_REJECT_BUSY:-}" == "1" && -r "$FAKE_JHW_ACTIVE" ]]; then
    exit 4
fi
{
    printf 'CONFIG=%s\\n' "${FAKE_CONFIG_LOADED:-}"
    printf 'ARG=%s\\n' "$@"
} > "$FAKE_JHW_LOG"
if [[ -n "${FAKE_CONTROL_EXIT:-}" ]]; then
    exit "$FAKE_CONTROL_EXIT"
fi
active_session=""
arguments=("$@")
for ((index = 0; index < ${#arguments[@]}; index++)); do
    if [[ "${arguments[$index]}" == "--session" ]]; then
        active_session="${arguments[$((index + 1))]}"
        break
    fi
done
[[ -n "$active_session" ]] || exit 96
printf '%s\\n' "$active_session" > "$FAKE_JHW_ACTIVE"
while [[ $# -gt 0 && "$1" != "--" ]]; do
    shift
done
[[ "${1:-}" == "--" ]] || exit 97
shift
"$@"
child_exit=$?
rm -f -- "$FAKE_JHW_ACTIVE"
exit "$child_exit"
"""


def _control_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    control = tmp_path / "jhw-control"
    control.write_text(FAKE_CONTROL, encoding="utf-8")
    control.chmod(0o755)
    config = tmp_path / "control.env"
    config.write_text("FAKE_CONFIG_LOADED=from-config\n", encoding="utf-8")
    log = tmp_path / "control-args.log"
    env = os.environ.copy()
    for key in (
        "PIM_BOARD_LOCK_HELD",
        "PIM_BOARD_LOCK_OWNER_PID",
        "PIM_BOARD_LOCK_SESSION",
        "PIM_BOARD_LOCK_BOARD_ID",
        "PIM_BOARD_SESSION",
        "FAKE_GRANTED_UNTIL",
        "FAKE_REJECT_BUSY",
        "FAKE_STATUS_PURPOSE",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_REPOSITORY",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
    ):
        env.pop(key, None)
    env.update(
        {
            "HOME": str(tmp_path),
            "USER": "pytest-user",
            "JHW_CONTROL_BIN": str(control),
            "JHW_CONTROL_ENV": str(config),
            "FAKE_JHW_LOG": str(log),
            "FAKE_JHW_ACTIVE": str(tmp_path / "active-session"),
        }
    )
    return env, log


def _logged_args(log: Path) -> list[str]:
    return [line.removeprefix("ARG=") for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("ARG=")]


def test_wrapper_loads_config_acquires_exclusive_and_preserves_child_exit(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    env["PIM_BOARD_SESSION"] = "pytest-session"
    result = subprocess.run(
        [
            str(WRAPPER),
            "--for",
            "30m",
            "--purpose",
            "pytest wrapper",
            "--",
            "sh",
            "-c",
            'set -u; test "$PIM_BOARD_LOCK_HELD" = 1; '
            'test -n "$PIM_BOARD_LOCK_OWNER_PID"; '
            'test "$PIM_BOARD_LOCK_SESSION" = pytest-session; '
            'test "$PIM_BOARD_LOCK_BOARD_ID" = pim; exit 7',
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 7
    assert log.read_text(encoding="utf-8").splitlines()[0] == "CONFIG=from-config"
    args = _logged_args(log)
    assert args[:5] == ["board", "with", "pim", "--mode", "exclusive"]
    assert args[args.index("--for") + 1] == "30m"
    assert args[args.index("--session") + 1] == "pytest-session"
    assert args[args.index("--purpose") + 1] == "pytest wrapper"
    child_index = args.index("--")
    child = args[child_index + 1 :]
    assert child[:2] == ["env", "PIM_BOARD_LOCK_HELD=1"]
    assert re.fullmatch(r"PIM_BOARD_LOCK_OWNER_PID=\d+", child[2])
    assert child[3:5] == [
        "PIM_BOARD_LOCK_SESSION=pytest-session",
        "PIM_BOARD_LOCK_BOARD_ID=pim",
    ]
    assert child[5:7] == ["sh", "-c"]


def test_wrapper_derives_github_session_and_passes_long_lease(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    env.update({"GITHUB_REPOSITORY": "jhw7500/pim-check", "GITHUB_RUN_ID": "1234", "GITHUB_RUN_ATTEMPT": "2"})
    target = datetime.now(timezone.utc) + timedelta(hours=1)
    target_text = target.isoformat()
    result = subprocess.run([str(WRAPPER), "--until", target_text, "--purpose", "github test", "--long-lease", "true", "--", "true"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    args = _logged_args(log)
    assert args[args.index("--session") + 1] == "github:jhw7500/pim-check:1234:2"
    assert args[args.index("--until") + 1] == target_text
    assert args[args.index("--long-lease") + 1] == "true"
    assert str(DEADLINE_SUPERVISOR) in args
    deadline = int(args[args.index("--deadline-epoch") + 1])
    assert int(target.timestamp()) - 1 <= deadline <= int(target.timestamp())


def test_wrapper_reserves_worst_case_teardown_before_lease_expiry(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    target = datetime.now(timezone.utc) + timedelta(hours=2)

    result = subprocess.run(
        [
            str(WRAPPER),
            "--until",
            target.isoformat(),
            "--purpose",
            "teardown budget test",
            "--long-lease",
            "true",
            "--",
            "true",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    args = _logged_args(log)
    cleanup_margin = int(args[args.index("--cleanup-margin-seconds") + 1])
    term_grace = int(args[args.index("--term-grace-seconds") + 1])
    assert term_grace >= MIN_AUTOMATION_TERM_GRACE_SECONDS
    assert cleanup_margin - term_grace >= MIN_LEASE_RELEASE_MARGIN_SECONDS


def _run_deadline_supervisor(
    deadline: float,
    cleanup_margin: float,
    term_grace: float,
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(DEADLINE_SUPERVISOR),
            "--deadline-epoch",
            str(deadline),
            "--cleanup-margin-seconds",
            str(cleanup_margin),
            "--term-grace-seconds",
            str(term_grace),
            "--",
            *command,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def test_deadline_supervisor_preserves_nonnegative_child_exit() -> None:
    result = _run_deadline_supervisor(
        time.time() + 5,
        cleanup_margin=1,
        term_grace=0.3,
        command=[sys.executable, "-c", "raise SystemExit(7)"],
    )

    assert result.returncode == 7


@pytest.mark.parametrize(
    "signal_name, with_descendant, expected_exit",
    [
        ("SIGTERM", False, 143),
        ("SIGSEGV", True, 139),
    ],
)
def test_deadline_supervisor_normalizes_child_signal_exit(
    signal_name: str,
    with_descendant: bool,
    expected_exit: int,
    tmp_path: Path,
) -> None:
    descendant_pid = tmp_path / "signal-child-descendant.pid"
    descendant = """
import os
import sys
import time
from pathlib import Path

Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(60)
"""
    child = """
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

marker = Path(sys.argv[2])
if sys.argv[3] == "descendant":
    subprocess.Popen([sys.executable, "-c", sys.argv[4], str(marker)])
    limit = time.time() + 2
    while time.time() < limit and not marker.exists():
        time.sleep(0.01)
    if not marker.exists():
        raise SystemExit(70)
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
os.kill(os.getpid(), getattr(signal, sys.argv[1]))
"""

    result = _run_deadline_supervisor(
        time.time() + 5,
        cleanup_margin=1,
        term_grace=0.3,
        command=[
            sys.executable,
            "-c",
            child,
            signal_name,
            str(descendant_pid),
            "descendant" if with_descendant else "none",
            descendant,
        ],
    )

    assert result.returncode == expected_exit
    if with_descendant:
        pid = int(descendant_pid.read_text(encoding="utf-8"))
        assert not Path(f"/proc/{pid}").exists()
        assert "descendants still running" in result.stderr.lower()


def test_deadline_supervisor_runs_teardown_and_returns_timeout_before_lease_expiry(
    tmp_path: Path,
) -> None:
    started = tmp_path / "started"
    teardown = tmp_path / "teardown"
    deadline = time.time() + 1.2
    child = """
import signal
import sys
import time
from pathlib import Path

started = Path(sys.argv[1])
teardown = Path(sys.argv[2])

def on_term(_signum, _frame):
    teardown.write_text("complete", encoding="utf-8")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, on_term)
started.write_text(str(__import__("os").getpid()), encoding="utf-8")
time.sleep(60)
"""

    result = _run_deadline_supervisor(
        deadline,
        cleanup_margin=0.7,
        term_grace=0.3,
        command=[sys.executable, "-c", child, str(started), str(teardown)],
    )

    assert result.returncode == 124
    assert started.exists()
    assert teardown.read_text(encoding="utf-8") == "complete"
    assert time.time() < deadline
    assert "deadline" in result.stderr.lower()


def test_deadline_supervisor_kills_and_reaps_hung_process_tree(tmp_path: Path) -> None:
    parent_pid = tmp_path / "parent.pid"
    descendant_pid = tmp_path / "descendant.pid"
    deadline = time.time() + 1.4
    descendant = """
import os
import signal
import sys
import time
from pathlib import Path

signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(60)
"""
    parent = """
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
subprocess.Popen([sys.executable, "-c", sys.argv[3], sys.argv[2]])
time.sleep(60)
"""

    result = _run_deadline_supervisor(
        deadline,
        cleanup_margin=0.8,
        term_grace=0.2,
        command=[
            sys.executable,
            "-c",
            parent,
            str(parent_pid),
            str(descendant_pid),
            descendant,
        ],
    )

    assert result.returncode == 124
    pids = [
        int(parent_pid.read_text(encoding="utf-8")),
        int(descendant_pid.read_text(encoding="utf-8")),
    ]
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
    assert time.time() < deadline


def test_deadline_supervisor_reaps_process_tree_when_wrapper_forwards_term(
    tmp_path: Path,
) -> None:
    parent_pid = tmp_path / "signal-parent.pid"
    descendant_pid = tmp_path / "signal-descendant.pid"
    descendant = """
import os
import signal
import sys
import time
from pathlib import Path

signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(60)
"""
    parent = """
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
subprocess.Popen([sys.executable, "-c", sys.argv[3], sys.argv[2]])
time.sleep(60)
"""
    command = [
        sys.executable,
        str(DEADLINE_SUPERVISOR),
        "--deadline-epoch",
        str(time.time() + 30),
        "--cleanup-margin-seconds",
        "5",
        "--term-grace-seconds",
        "0.2",
        "--",
        sys.executable,
        "-c",
        parent,
        str(parent_pid),
        str(descendant_pid),
        descendant,
    ]
    supervisor = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    limit = time.time() + 2
    while time.time() < limit and not descendant_pid.exists():
        time.sleep(0.01)
    assert parent_pid.exists() and descendant_pid.exists()

    supervisor.terminate()
    try:
        _stdout, stderr = supervisor.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process_group = int(parent_pid.read_text(encoding="utf-8"))
        os.killpg(process_group, signal.SIGKILL)
        supervisor.kill()
        supervisor.wait(timeout=2)
        pytest.fail("deadline supervisor did not react to forwarded SIGTERM")

    assert supervisor.returncode == 128 + signal.SIGTERM
    assert "received signal" in stderr.lower()
    pids = [
        int(parent_pid.read_text(encoding="utf-8")),
        int(descendant_pid.read_text(encoding="utf-8")),
    ]
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)


@pytest.mark.parametrize(
    "supervisor_signal, expected_child_signal",
    [
        (signal.SIGINT, signal.SIGINT),
        (signal.SIGTERM, signal.SIGTERM),
        (signal.SIGHUP, signal.SIGTERM),
    ],
)
def test_deadline_supervisor_forwards_external_signal_once(
    supervisor_signal: int,
    expected_child_signal: int,
    tmp_path: Path,
) -> None:
    child_pid = tmp_path / "single-signal-child.pid"
    received = tmp_path / "received-signals"
    teardown = tmp_path / "teardown-complete"
    child = """
import os
import signal
import sys
import time
from pathlib import Path

child_pid = Path(sys.argv[1])
received = Path(sys.argv[2])
teardown = Path(sys.argv[3])
seen = []

def on_signal(signum, _frame):
    seen.append(signum)
    received.write_text(",".join(str(item) for item in seen), encoding="utf-8")
    if len(seen) > 1:
        raise SystemExit(91)
    time.sleep(0.3)
    teardown.write_text("complete", encoding="utf-8")
    raise SystemExit(0)

signal.signal(signal.SIGINT, on_signal)
signal.signal(signal.SIGTERM, on_signal)
child_pid.write_text(str(os.getpid()), encoding="utf-8")
time.sleep(60)
"""
    supervisor = subprocess.Popen(
        [
            sys.executable,
            str(DEADLINE_SUPERVISOR),
            "--deadline-epoch",
            str(time.time() + 30),
            "--cleanup-margin-seconds",
            "5",
            "--term-grace-seconds",
            "1",
            "--",
            sys.executable,
            "-c",
            child,
            str(child_pid),
            str(received),
            str(teardown),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    process_group: Optional[int] = None
    try:
        limit = time.time() + 2
        while time.time() < limit and not child_pid.exists():
            time.sleep(0.01)
        assert child_pid.exists()
        process_group = int(child_pid.read_text(encoding="utf-8"))

        supervisor.send_signal(supervisor_signal)
        try:
            supervisor.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            pytest.fail("deadline supervisor did not finish signal teardown")

        assert supervisor.returncode == 128 + supervisor_signal
        assert received.read_text(encoding="utf-8") == str(
            int(expected_child_signal)
        )
        assert teardown.read_text(encoding="utf-8") == "complete"
        assert not Path(f"/proc/{process_group}").exists()
    finally:
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if supervisor.poll() is None:
            supervisor.kill()
        supervisor.wait(timeout=2)


def test_deadline_supervisor_refuses_to_start_without_cleanup_budget(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-start"

    result = _run_deadline_supervisor(
        time.time() + 0.1,
        cleanup_margin=0.5,
        term_grace=0.1,
        command=[sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).touch()", str(marker)],
    )

    assert result.returncode == 124
    assert not marker.exists()
    assert "insufficient" in result.stderr.lower()


def test_wrapper_derives_codex_then_local_session_fallbacks(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    env["CODEX_THREAD_ID"] = "thread-abc"
    codex = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "codex", "--", "true"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert codex.returncode == 0
    assert _logged_args(log)[_logged_args(log).index("--session") + 1] == "codex:thread-abc"
    env.pop("CODEX_THREAD_ID")
    local = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "local", "--", "true"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert local.returncode == 0
    args = _logged_args(log)
    assert re.fullmatch(r"local:pytest-user:\d+", args[args.index("--session") + 1])


def test_wrapper_propagates_board_busy_without_starting_child(tmp_path: Path) -> None:
    env, _ = _control_env(tmp_path)
    marker = tmp_path / "child-started"
    env["FAKE_CONTROL_EXIT"] = "4"
    result = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "busy test", "--", "sh", "-c", f"touch {marker}"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 4
    assert not marker.exists()


def test_wrapper_does_not_trust_a_caller_supplied_marker(tmp_path: Path) -> None:
    child_started = tmp_path / "child-started"
    env = os.environ.copy()
    env.update({"PIM_BOARD_LOCK_HELD": "1", "JHW_CONTROL_BIN": str(tmp_path / "missing-control"), "JHW_CONTROL_ENV": str(tmp_path / "missing.env")})
    result = subprocess.run(
        [
            str(WRAPPER),
            "--for",
            "30m",
            "--purpose",
            "forged marker",
            "--",
            "sh",
            "-c",
            f"touch {child_started}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 64
    assert not child_started.exists()


def test_wrapper_reuses_a_verified_active_lease_for_nested_calls(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    env["PIM_BOARD_SESSION"] = "pytest:nested"

    result = subprocess.run(
        [
            str(WRAPPER),
            "--for",
            "30m",
            "--purpose",
            "outer",
            "--",
            str(WRAPPER),
            "--for",
            "30m",
            "--purpose",
            "inner",
            "--",
            "sh",
            "-c",
            "exit 9",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 9
    assert log.read_text(encoding="utf-8").count("CONFIG=") == 1


def test_wrapper_strict_probe_accepts_its_own_long_lease(tmp_path: Path) -> None:
    env, _ = _control_env(tmp_path)
    purpose = "pim-check auto_overnight"
    target = datetime.now(timezone.utc) + timedelta(hours=2)
    target_text = target.isoformat()
    env.update(
        {
            "PIM_BOARD_SESSION": "pytest:strict-long-lease",
            "FAKE_STATUS_PURPOSE": purpose,
            "FAKE_GRANTED_UNTIL": target_text,
        }
    )

    result = subprocess.run(
        [
            str(WRAPPER),
            "--until",
            target_text,
            "--purpose",
            purpose,
            "--long-lease",
            "true",
            "--",
            str(WRAPPER),
            "--check-held",
            "--until",
            target_text,
            "--purpose",
            purpose,
            "--long-lease",
            "true",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0


@pytest.mark.parametrize("status_mismatch", ["purpose", "deadline"])
def test_wrapper_strict_probe_rejects_noncovering_status(
    status_mismatch: str,
    tmp_path: Path,
) -> None:
    env, _ = _control_env(tmp_path)
    purpose = "pim-check auto_overnight"
    now = datetime.now(timezone.utc)
    target = now + timedelta(hours=2)
    target_text = target.isoformat()
    env.update(
        {
            "PIM_BOARD_SESSION": "pytest:strict-status",
            "FAKE_STATUS_PURPOSE": (
                "different automation" if status_mismatch == "purpose" else purpose
            ),
            "FAKE_GRANTED_UNTIL": (
                (now + timedelta(minutes=30)).isoformat()
                if status_mismatch == "deadline"
                else target_text
            ),
        }
    )

    result = subprocess.run(
        [
            str(WRAPPER),
            "--until",
            target_text,
            "--purpose",
            purpose,
            "--long-lease",
            "true",
            "--",
            str(WRAPPER),
            "--check-held",
            "--until",
            target_text,
            "--purpose",
            purpose,
            "--long-lease",
            "true",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1


def test_wrapper_does_not_reuse_short_lease_for_long_automation(tmp_path: Path) -> None:
    child_started = tmp_path / "child-started"
    env, _ = _control_env(tmp_path)
    purpose = "pim-check auto_chain"
    target = datetime.now(timezone.utc) + timedelta(hours=2)
    target_text = target.isoformat()
    env.update(
        {
            "PIM_BOARD_SESSION": "pytest:short-outer",
            "FAKE_STATUS_PURPOSE": purpose,
            "FAKE_GRANTED_UNTIL": target_text,
            "FAKE_REJECT_BUSY": "1",
        }
    )

    result = subprocess.run(
        [
            str(WRAPPER),
            "--for",
            "30m",
            "--purpose",
            purpose,
            "--",
            str(WRAPPER),
            "--until",
            target_text,
            "--purpose",
            purpose,
            "--long-lease",
            "true",
            "--",
            "sh",
            "-c",
            f"touch {child_started}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert not child_started.exists()


@pytest.mark.parametrize("missing, message", [("config", "not readable"), ("binary", "not executable")])
def test_wrapper_reports_missing_control_dependency(tmp_path: Path, missing: str, message: str) -> None:
    env, _ = _control_env(tmp_path)
    if missing == "config":
        env["JHW_CONTROL_ENV"] = str(tmp_path / "missing.env")
    else:
        env["JHW_CONTROL_BIN"] = str(tmp_path / "missing-control")
    result = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "missing", "--", "true"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 64
    assert message in result.stderr.lower()


@pytest.mark.parametrize("arguments, message", [
    (["--purpose", "missing lease", "--", "true"], "exactly one"),
    (["--for", "30m", "--until", "2026-08-25T09:00:00+09:00", "--purpose", "two", "--", "true"], "exactly one"),
    (["--for", "30m", "--", "true"], "purpose"),
    (["--for", "30m", "--purpose", "missing child", "--"], "child command"),
    (["--for", "30m", "--purpose", "bad bool", "--long-lease", "false", "--", "true"], "exact literal true"),
])
def test_wrapper_rejects_invalid_contract(arguments: list[str], message: str) -> None:
    result = subprocess.run([str(WRAPPER), *arguments], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 64
    assert message in result.stderr.lower()


def _run_automation(
    script_name: str,
    tmp_path: Path,
    extra_env: Optional[dict[str, str]] = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    env, log = _control_env(tmp_path)
    env.update(
        {
            "PIM_BOARD_SESSION": f"pytest:{script_name}",
            "FAKE_CONTROL_EXIT": "4",
            "TZ": "Asia/Seoul",
        }
    )
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / script_name)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result, _logged_args(log)


def test_auto_chain_self_wraps_with_24_hour_long_lease(tmp_path: Path) -> None:
    target = datetime(2026, 8, 26, 9, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    result, args = _run_automation(
        "auto_chain.sh",
        tmp_path,
        {"PIM_AUTOMATION_TARGET_END": str(int(target.timestamp()))},
    )

    assert result.returncode == 4
    assert args[args.index("--until") + 1] == target.isoformat()
    assert args[args.index("--long-lease") + 1] == "true"
    assert "auto_chain" in args[args.index("--purpose") + 1]


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def test_auto_chain_fails_visibly_when_legacy_plan_is_running(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    automation = scripts / "auto_chain.sh"
    shutil.copy2(ROOT / "scripts" / "auto_chain.sh", automation)
    _write_executable(
        scripts / "with_pim_board.sh",
        "#!/bin/sh\n"
        'test "${1:-}" = --check-held && exit 0\n'
        "exit 99\n",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "pgrep-seen"
    _write_executable(
        fake_bin / "pgrep",
        """#!/bin/sh
if [ ! -e "$FAKE_PGREP_STATE" ]; then
    touch "$FAKE_PGREP_STATE"
    exit 0
fi
kill -TERM "$PPID"
exit 1
""",
    )
    _write_executable(fake_bin / "sleep", "#!/bin/sh\nexit 0\n")
    env = os.environ.copy()
    env.update(
        {
            "PIM_BOARD_LOCK_HELD": "1",
            "FAKE_PGREP_STATE": str(state),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", str(automation)],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 75
    assert "legacy" in result.stdout.lower()


def test_auto_chain_does_not_trust_a_caller_supplied_marker(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    automation = scripts / "auto_chain.sh"
    shutil.copy2(ROOT / "scripts" / "auto_chain.sh", automation)
    _write_executable(
        scripts / "with_pim_board.sh",
        "#!/bin/sh\n"
        'test "${1:-}" = --check-held && exit 1\n'
        "exit 4\n",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    unsafe_entry = tmp_path / "unsafe-entry"
    _write_executable(
        fake_bin / "pgrep",
        f"#!/bin/sh\ntouch {unsafe_entry}\nexit 0\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "PIM_BOARD_LOCK_HELD": "1",
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", str(automation)],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 4
    assert not unsafe_entry.exists()


@pytest.mark.parametrize("script_name", ["auto_overnight.sh", "auto_weekend.sh"])
def test_timed_automation_self_wraps_until_exact_deadline(script_name: str, tmp_path: Path) -> None:
    kst = timezone(timedelta(hours=9))
    target = datetime(2026, 8, 25, 9, 0, 0, tzinfo=kst)
    result, args = _run_automation(
        script_name,
        tmp_path,
        {"PIM_AUTOMATION_TARGET_END": str(int(target.timestamp()))},
    )

    assert result.returncode == 4
    assert args[args.index("--until") + 1] == target.isoformat()
    assert args[args.index("--long-lease") + 1] == "true"
    assert script_name.removesuffix(".sh") in args[args.index("--purpose") + 1]


@pytest.mark.parametrize("script_name", ["auto_chain.sh", "auto_overnight.sh", "auto_weekend.sh"])
def test_automation_probes_for_its_exact_long_lease(script_name: str, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    automation = scripts / script_name
    shutil.copy2(ROOT / "scripts" / script_name, automation)
    call_log = tmp_path / "wrapper-calls.log"
    _write_executable(
        scripts / "with_pim_board.sh",
        "#!/bin/sh\n"
        "{\n"
        "  printf 'CALL\\n'\n"
        "  printf 'ARG=%s\\n' \"$@\"\n"
        "  printf 'END\\n'\n"
        '} >> "$WRAPPER_CALL_LOG"\n'
        "exit 4\n",
    )
    kst = timezone(timedelta(hours=9))
    target = datetime(2026, 8, 26, 9, 0, 0, tzinfo=kst)
    env = os.environ.copy()
    env.update(
        {
            "PIM_AUTOMATION_TARGET_END": str(int(target.timestamp())),
            "TZ": "Asia/Seoul",
            "WRAPPER_CALL_LOG": str(call_log),
        }
    )

    result = subprocess.run(
        ["bash", str(automation)],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    calls = [
        [line.removeprefix("ARG=") for line in block.splitlines() if line.startswith("ARG=")]
        for block in call_log.read_text(encoding="utf-8").split("CALL\n")[1:]
    ]
    purpose = f"pim-check {script_name.removesuffix('.sh')}"
    required = [
        "--until",
        target.isoformat(),
        "--purpose",
        purpose,
        "--long-lease",
        "true",
    ]

    assert result.returncode == 4
    assert calls[0] == ["--check-held", *required]
    assert calls[1][: len(required)] == required


@pytest.mark.parametrize("script_name", ["auto_chain.sh", "auto_overnight.sh", "auto_weekend.sh"])
def test_automation_uses_its_own_checkout_before_run_state(script_name: str) -> None:
    text = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")

    assert "PROJECT=/home/jhw/ai/opencode/projects/pim-check" not in text
    assert text.index("with_pim_board.sh") < text.index("SESSION_TS=")


def _workflow(path: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / path).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "path, job_name, step_name, lease, purpose",
    [
        (
            "hw-verify.yml",
            "mixed-combo",
            "Run mixed_combo verification (4 tests × 10 channel registers)",
            "30m",
            "github:${{ github.workflow }}:${{ github.run_id }}:${{ github.run_attempt }}",
        ),
        (
            "hw-verify-comprehensive.yml",
            "comprehensive",
            "Run comprehensive verification (96 tests, ~2h)",
            "3h",
            "github:${{ github.workflow }}:${{ github.run_id }}:${{ github.run_attempt }}",
        ),
        (
            "hw-verify-plan.yml",
            "plan-run",
            "Run plan",
            "12h",
            "github:${{ github.workflow }}:${{ github.run_id }}:${{ github.run_attempt }}:${{ inputs.plan }}",
        ),
        (
            "hw-evidence-measure.yml",
            "measure",
            "Run leased hardware evidence",
            "3h",
            "github:${{ github.workflow }}:${{ github.run_id }}:${{ github.run_attempt }}:hw-evidence",
        ),
    ],
)
def test_hardware_workflow_uses_common_fail_fast_wrapper(
    path: str,
    job_name: str,
    step_name: str,
    lease: str,
    purpose: str,
) -> None:
    workflow = _workflow(path)
    assert workflow["concurrency"] == {"group": "pim-target-lock", "cancel-in-progress": False}
    step = next(item for item in workflow["jobs"][job_name]["steps"] if item.get("name") == step_name)
    command = step["run"]

    assert command.count("scripts/with_pim_board.sh") == 1
    assert f"--for {lease}" in command
    assert f'--purpose "{purpose}"' in command
    assert "--long-lease" not in command
    assert "board wait" not in command
    assert "|| true" not in command


def test_release_plan_wraps_selected_command_and_keeps_warn_policy() -> None:
    workflow = _workflow("hw-verify-plan.yml")
    step = next(item for item in workflow["jobs"]["plan-run"]["steps"] if item.get("name") == "Run plan")
    command = step["run"]

    assert "PLAN_COMMAND=(python3 run_mixed_combo_verify.py)" in command
    assert "PLAN_COMMAND=(python3 run_bps_quick.py)" in command
    assert 'PLAN_COMMAND=(python3 pim_check.py --plan "${{ inputs.plan }}" --host "${{ env.TARGET_HOST }}")' in command
    assert '"${PLAN_COMMAND[@]}"' in command
    assert "if [ $rc -eq 0 ] || [ $rc -eq 2 ]; then" in command
    assert "exit $rc" in command


@pytest.mark.parametrize(
    ("path", "job_name"),
    [("hw-verify.yml", "mixed-combo"), ("hw-verify-plan.yml", "plan-run")],
)
def test_compatibility_workflows_use_baseline_wired_target(
    path: str,
    job_name: str,
) -> None:
    """Compatibility jobs must measure the board that owns the committed baseline."""
    workflow = _workflow(path)

    assert workflow["env"]["TARGET_HOST"] == "192.168.214.4"
    precheck = next(
        item for item in workflow["jobs"][job_name]["steps"]
        if item.get("name") == "Target reachability precheck"
    )["run"]
    assert "192.168.0.5" not in precheck
    assert "${{ env.TARGET_HOST }}" in precheck


@pytest.mark.parametrize(
    ("payload", "expected_lines"),
    [
        (
            {
                "verdict": "PASS",
                "metrics": [
                    {"id": "mixed_combo.test1.bus1.mode_mask", "verdict": "PASS"},
                    {"id": "mixed_combo.test1.ch1.rotation", "verdict": "PASS"},
                ],
                "errors": [],
            },
            ("Verdict: PASS", "Metrics: 2/2 PASS"),
        ),
        (
            {
                "verdict": "ERROR",
                "metrics": [
                    {"id": "mixed_combo.test1.bus1.mode_mask", "verdict": "PASS"},
                    {"id": "mixed_combo.test1.ch1.rotation", "verdict": "FAIL"},
                ],
                "errors": [{"code": "mixed_combo.collection_failed", "message": "no evidence"}],
            },
            (
                "Verdict: ERROR",
                "Metrics: 1/2 PASS",
                "Error: mixed_combo.collection_failed — no evidence",
            ),
        ),
        (
            {"verdict": "ERROR", "metrics": ["malformed", {"verdict": "PASS"}], "errors": []},
            ("Verdict: ERROR", "Metrics: 1/2 PASS"),
        ),
    ],
    ids=("pass", "error", "malformed-metric"),
)
def test_mixed_combo_workflow_summary_consumes_gate_result(
    tmp_path: Path,
    payload: dict,
    expected_lines: tuple[str, ...],
) -> None:
    """The always-run Summary must render the adapter gate dict without crashing."""
    workflow = _workflow("hw-verify.yml")
    summary = next(
        item for item in workflow["jobs"]["mixed-combo"]["steps"]
        if item.get("name") == "Summary"
    )["run"]
    (tmp_path / "mixed_combo_results.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", "-c", summary],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    for line in expected_lines:
        assert line in completed.stdout
