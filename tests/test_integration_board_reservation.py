"""Cross-entry-point contract for the shared PIM board reservation."""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "with_pim_board.sh"

FAKE_CONTROL = """#!/usr/bin/env bash
set -u
{
    printf 'CONFIG=%s\\n' "${FAKE_CONFIG_LOADED:-}"
    printf 'ARG=%s\\n' "$@"
} > "$FAKE_JHW_LOG"
if [[ -n "${FAKE_CONTROL_EXIT:-}" ]]; then
    exit "$FAKE_CONTROL_EXIT"
fi
while [[ $# -gt 0 && "$1" != "--" ]]; do
    shift
done
[[ "${1:-}" == "--" ]] || exit 97
shift
exec "$@"
"""


def _control_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    control = tmp_path / "jhw-control"
    control.write_text(FAKE_CONTROL, encoding="utf-8")
    control.chmod(0o755)
    config = tmp_path / "control.env"
    config.write_text("FAKE_CONFIG_LOADED=from-config\n", encoding="utf-8")
    log = tmp_path / "control-args.log"
    env = os.environ.copy()
    for key in ("PIM_BOARD_LOCK_HELD", "PIM_BOARD_SESSION", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY", "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        env.pop(key, None)
    env.update({"HOME": str(tmp_path), "USER": "pytest-user", "JHW_CONTROL_BIN": str(control), "JHW_CONTROL_ENV": str(config), "FAKE_JHW_LOG": str(log)})
    return env, log


def _logged_args(log: Path) -> list[str]:
    return [line.removeprefix("ARG=") for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("ARG=")]


def test_wrapper_loads_config_acquires_exclusive_and_preserves_child_exit(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    env["PIM_BOARD_SESSION"] = "pytest-session"
    result = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "pytest wrapper", "--", "sh", "-c", 'test "$PIM_BOARD_LOCK_HELD" = 1; exit 7'], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 7
    assert log.read_text(encoding="utf-8").splitlines()[0] == "CONFIG=from-config"
    args = _logged_args(log)
    assert args[:5] == ["board", "with", "pim", "--mode", "exclusive"]
    assert args[args.index("--for") + 1] == "30m"
    assert args[args.index("--session") + 1] == "pytest-session"
    assert args[args.index("--purpose") + 1] == "pytest wrapper"
    assert args[-6:] == ["--", "env", "PIM_BOARD_LOCK_HELD=1", "sh", "-c", 'test "$PIM_BOARD_LOCK_HELD" = 1; exit 7']


def test_wrapper_derives_github_session_and_passes_long_lease(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    env.update({"GITHUB_REPOSITORY": "jhw7500/pim-check", "GITHUB_RUN_ID": "1234", "GITHUB_RUN_ATTEMPT": "2"})
    result = subprocess.run([str(WRAPPER), "--until", "2026-08-25T09:00:00+09:00", "--purpose", "github test", "--long-lease", "true", "--", "true"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    args = _logged_args(log)
    assert args[args.index("--session") + 1] == "github:jhw7500/pim-check:1234:2"
    assert args[args.index("--until") + 1] == "2026-08-25T09:00:00+09:00"
    assert args[args.index("--long-lease") + 1] == "true"


def test_wrapper_derives_codex_then_local_session_fallbacks(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path); env["CODEX_THREAD_ID"] = "thread-abc"
    codex = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "codex", "--", "true"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert codex.returncode == 0
    assert _logged_args(log)[_logged_args(log).index("--session") + 1] == "codex:thread-abc"
    env.pop("CODEX_THREAD_ID")
    local = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "local", "--", "true"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert local.returncode == 0
    args = _logged_args(log)
    assert re.fullmatch(r"local:pytest-user:\d+", args[args.index("--session") + 1])


def test_wrapper_propagates_board_busy_without_starting_child(tmp_path: Path) -> None:
    env, _ = _control_env(tmp_path); marker = tmp_path / "child-started"; env["FAKE_CONTROL_EXIT"] = "4"
    result = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "busy test", "--", "sh", "-c", f"touch {marker}"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 4
    assert not marker.exists()


def test_wrapper_reuses_existing_marker_without_control_files(tmp_path: Path) -> None:
    env = os.environ.copy(); env.update({"PIM_BOARD_LOCK_HELD": "1", "JHW_CONTROL_BIN": str(tmp_path / "missing-control"), "JHW_CONTROL_ENV": str(tmp_path / "missing.env")})
    result = subprocess.run([str(WRAPPER), "--for", "30m", "--purpose", "nested", "--", "sh", "-c", "exit 9"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 9


@pytest.mark.parametrize("missing, message", [("config", "not readable"), ("binary", "not executable")])
def test_wrapper_reports_missing_control_dependency(tmp_path: Path, missing: str, message: str) -> None:
    env, _ = _control_env(tmp_path)
    if missing == "config": env["JHW_CONTROL_ENV"] = str(tmp_path / "missing.env")
    else: env["JHW_CONTROL_BIN"] = str(tmp_path / "missing-control")
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
    result, args = _run_automation("auto_chain.sh", tmp_path)

    assert result.returncode == 4
    assert args[args.index("--for") + 1] == "24h"
    assert args[args.index("--long-lease") + 1] == "true"
    assert "auto_chain" in args[args.index("--purpose") + 1]


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
def test_automation_uses_its_own_checkout_before_run_state(script_name: str) -> None:
    text = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")

    assert "PROJECT=/home/jhw/ai/opencode/projects/pim-check" not in text
    assert text.index("with_pim_board.sh") < text.index("SESSION_TS=")
