# PIM Board Lock Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every CI workflow and local plan/automation entry point named in issue #108 acquire the same fail-fast exclusive `pim` board lease before changing or rebooting the target.

**Architecture:** A repository-local Bash wrapper is the single adapter between pim-check and the host-installed Project Control CLI. GitHub workflows and long-running automation call that wrapper, while a committed Claude `PreToolUse` guard blocks the known direct plan/runner commands and operational documentation teaches the same canonical path.

**Tech Stack:** Bash, Python 3.9+, pytest, PyYAML, GitHub Actions YAML, Claude Code project hooks, `jhw-control board with`

**Spec:** `docs/superpowers/specs/2026-08-24-pim-board-lock-integration-design.md`

## Global Constraints

- Keep `concurrency.group: pim-target-lock` and `cancel-in-progress: false` in all three hardware workflows.
- Acquire board `pim` in `exclusive` mode with `board with`; never use `board wait`, retries, success-on-busy, or `|| true` around acquisition.
- A busy board must retain Project Control exit code `4`; wrapped child exit codes must propagate unchanged.
- Load `${JHW_CONTROL_ENV:-$HOME/.config/jhw-control/control.env}` without printing it and invoke `${JHW_CONTROL_BIN:-$HOME/.local/bin/jhw-control}` explicitly.
- Default leases are capped at 12 hours. Only the local long-running automation opts into the 72-hour ceiling with the exact CLI pair `--long-lease true`.
- Do not acquire the real board or require a target in automated tests; use a temporary executable fake `jhw-control`.
- Python remains compatible with 3.9 and gains no dependency beyond the existing standard library, pytest, and PyYAML.
- The legacy web dashboard, Docker runner, and non-plan CLI modes are outside issue #108 and this plan; do not imply they are protected.
- Run shell commands through `rtk` per the repository RTK policy.
- Immediately before push, PR, merge, or deploy, run `jhw-control task assert-owner --task tsk-01a03249-be8c-7759-bd5e-f2fa18918799 --claim clm-01a03249-f551-7a75-b79e-478208360897` through `rtk` with the trusted control environment loaded.

---

## File Structure

- `scripts/with_pim_board.sh` — parse the repository lock contract, load trusted host configuration, derive an observable session, and `exec jhw-control board with`.
- `tests/test_integration_board_reservation.py` — fake-CLI behavioral tests for the wrapper plus repository contract tests for local automation and GitHub workflows.
- `scripts/auto_chain.sh`, `scripts/auto_overnight.sh`, `scripts/auto_weekend.sh` — self-reexec through the wrapper before creating run state or touching the target.
- `.github/workflows/hw-verify.yml`, `.github/workflows/hw-verify-comprehensive.yml`, `.github/workflows/hw-verify-plan.yml` — wrap only the hardware-mutating execution step while retaining GitHub concurrency and existing result policy.
- `scripts/guard_pim_board_command.py` — parse Claude Bash hook input and reject known direct plan or standalone hardware-runner commands.
- `tests/test_guard_pim_board_command.py` — prove hook exit semantics, compound-command handling, and false-positive exclusions.
- `.claude/settings.json` — register the guard as a project `PreToolUse` hook for Bash.
- `.gitignore` — keep local `.claude` files ignored while explicitly tracking project `settings.json`.
- `README.md`, `docs/realtime-monitor-guide.md`, `AGENTS.md`, `scripts/AGENTS.md`, `profiles/plans/AGENTS.md`, `CHANGELOG.md` — document the canonical wrapper, lease choices, fail-fast behavior, and advisory limits.

---

### Task 1: Implement the fail-fast board wrapper

**Files:**
- Create: `scripts/with_pim_board.sh`
- Create: `tests/test_integration_board_reservation.py`

**Interfaces:**
- Consumes: `jhw-control board with <board-id> --mode exclusive (--for <duration> | --until <instant>) --session <text> --purpose <text> [--long-lease true] -- <command>`
- Produces: `scripts/with_pim_board.sh (--for DURATION | --until TIMESTAMP) --purpose TEXT [--long-lease true] -- COMMAND [ARG ...]`
- Produces environment contract: `JHW_CONTROL_ENV`, `JHW_CONTROL_BIN`, `PIM_BOARD_ID`, `PIM_BOARD_SESSION`, and child marker `PIM_BOARD_LOCK_HELD=1`

- [ ] **Step 1: Write the fake control fixture and failing wrapper tests**

Create `tests/test_integration_board_reservation.py` with the following executable-boundary tests:

```python
"""Cross-entry-point contract for the shared PIM board reservation."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

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
    for key in (
        "PIM_BOARD_LOCK_HELD",
        "PIM_BOARD_SESSION",
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
        }
    )
    return env, log


def _logged_args(log: Path) -> list[str]:
    return [
        line.removeprefix("ARG=")
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("ARG=")
    ]


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
            'test "$PIM_BOARD_LOCK_HELD" = 1; exit 7',
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
    assert args[-6:] == [
        "--",
        "env",
        "PIM_BOARD_LOCK_HELD=1",
        "sh",
        "-c",
        'test "$PIM_BOARD_LOCK_HELD" = 1; exit 7',
    ]


def test_wrapper_derives_github_session_and_passes_long_lease(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    env.update(
        {
            "GITHUB_REPOSITORY": "jhw7500/pim-check",
            "GITHUB_RUN_ID": "1234",
            "GITHUB_RUN_ATTEMPT": "2",
        }
    )
    result = subprocess.run(
        [
            str(WRAPPER),
            "--until",
            "2026-08-25T09:00:00+09:00",
            "--purpose",
            "github test",
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
    assert args[args.index("--session") + 1] == "github:jhw7500/pim-check:1234:2"
    assert args[args.index("--until") + 1] == "2026-08-25T09:00:00+09:00"
    assert args[args.index("--long-lease") + 1] == "true"


def test_wrapper_derives_codex_then_local_session_fallbacks(tmp_path: Path) -> None:
    env, log = _control_env(tmp_path)
    env["CODEX_THREAD_ID"] = "thread-abc"
    codex = subprocess.run(
        [str(WRAPPER), "--for", "30m", "--purpose", "codex", "--", "true"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert codex.returncode == 0
    args = _logged_args(log)
    assert args[args.index("--session") + 1] == "codex:thread-abc"

    env.pop("CODEX_THREAD_ID")
    local = subprocess.run(
        [str(WRAPPER), "--for", "30m", "--purpose", "local", "--", "true"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert local.returncode == 0
    args = _logged_args(log)
    assert re.fullmatch(r"local:pytest-user:\d+", args[args.index("--session") + 1])


def test_wrapper_propagates_board_busy_without_starting_child(tmp_path: Path) -> None:
    env, _ = _control_env(tmp_path)
    marker = tmp_path / "child-started"
    env["FAKE_CONTROL_EXIT"] = "4"
    result = subprocess.run(
        [
            str(WRAPPER),
            "--for",
            "30m",
            "--purpose",
            "busy test",
            "--",
            "sh",
            "-c",
            f"touch {marker}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert not marker.exists()


def test_wrapper_reuses_existing_marker_without_control_files(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "PIM_BOARD_LOCK_HELD": "1",
            "JHW_CONTROL_BIN": str(tmp_path / "missing-control"),
            "JHW_CONTROL_ENV": str(tmp_path / "missing.env"),
        }
    )
    result = subprocess.run(
        [
            str(WRAPPER),
            "--for",
            "30m",
            "--purpose",
            "nested",
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


@pytest.mark.parametrize(
    "missing, message",
    [("config", "not readable"), ("binary", "not executable")],
)
def test_wrapper_reports_missing_control_dependency(tmp_path: Path, missing: str, message: str) -> None:
    env, _ = _control_env(tmp_path)
    if missing == "config":
        env["JHW_CONTROL_ENV"] = str(tmp_path / "missing.env")
    else:
        env["JHW_CONTROL_BIN"] = str(tmp_path / "missing-control")
    result = subprocess.run(
        [str(WRAPPER), "--for", "30m", "--purpose", "missing", "--", "true"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 64
    assert message in result.stderr.lower()


@pytest.mark.parametrize(
    "arguments, message",
    [
        (["--purpose", "missing lease", "--", "true"], "exactly one"),
        (["--for", "30m", "--until", "2026-08-25T09:00:00+09:00", "--purpose", "two", "--", "true"], "exactly one"),
        (["--for", "30m", "--", "true"], "purpose"),
        (["--for", "30m", "--purpose", "missing child", "--"], "child command"),
        (["--for", "30m", "--purpose", "bad bool", "--long-lease", "false", "--", "true"], "exact literal true"),
    ],
)
def test_wrapper_rejects_invalid_contract(arguments: list[str], message: str) -> None:
    result = subprocess.run(
        [str(WRAPPER), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 64
    assert message in result.stderr.lower()
```

- [ ] **Step 2: Run the wrapper tests to verify the red state**

Run:

```bash
rtk pytest -q tests/test_integration_board_reservation.py
```

Expected: FAIL because `scripts/with_pim_board.sh` does not exist.

- [ ] **Step 3: Implement the minimal wrapper**

Create `scripts/with_pim_board.sh` with this behavior and error vocabulary:

```bash
#!/usr/bin/env bash
set -uo pipefail

die() {
    printf 'with_pim_board: %s\n' "$*" >&2
    exit 64
}

lease_flag=""
lease_value=""
purpose=""
long_lease="false"
child=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --for|--until)
            [[ $# -ge 2 ]] || die "$1 requires a value"
            [[ -z "$lease_flag" ]] || die "exactly one of --for or --until is required"
            lease_flag="$1"
            lease_value="$2"
            shift 2
            ;;
        --purpose)
            [[ $# -ge 2 ]] || die "--purpose requires a value"
            purpose="$2"
            shift 2
            ;;
        --long-lease)
            [[ $# -ge 2 ]] || die "--long-lease requires the exact literal true"
            [[ "$2" == "true" ]] || die "--long-lease requires the exact literal true"
            long_lease="true"
            shift 2
            ;;
        --)
            shift
            child=("$@")
            break
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[[ -n "$lease_flag" ]] || die "exactly one of --for or --until is required"
[[ -n "$lease_value" ]] || die "$lease_flag requires a non-empty value"
[[ -n "$purpose" ]] || die "--purpose requires a non-empty value"
[[ ${#child[@]} -gt 0 ]] || die "child command is required after --"

if [[ "${PIM_BOARD_LOCK_HELD:-}" == "1" ]]; then
    exec "${child[@]}"
fi

[[ -n "${JHW_CONTROL_ENV:-}" || -n "${HOME:-}" ]] || die "HOME or JHW_CONTROL_ENV is required"
control_env="${JHW_CONTROL_ENV:-$HOME/.config/jhw-control/control.env}"
[[ -r "$control_env" ]] || die "control environment is not readable: $control_env"

set -a
# shellcheck disable=SC1090
if ! source "$control_env"; then
    set +a
    die "failed to load control environment: $control_env"
fi
set +a

[[ -n "${JHW_CONTROL_BIN:-}" || -n "${HOME:-}" ]] || die "HOME or JHW_CONTROL_BIN is required"
control_bin="${JHW_CONTROL_BIN:-$HOME/.local/bin/jhw-control}"
[[ -x "$control_bin" ]] || die "jhw-control is not executable: $control_bin"

if [[ -n "${PIM_BOARD_SESSION:-}" ]]; then
    board_session="$PIM_BOARD_SESSION"
elif [[ -n "${GITHUB_RUN_ID:-}" ]]; then
    board_session="github:${GITHUB_REPOSITORY:-pim-check}:${GITHUB_RUN_ID}:${GITHUB_RUN_ATTEMPT:-1}"
elif [[ -n "${CODEX_THREAD_ID:-}" ]]; then
    board_session="codex:${CODEX_THREAD_ID}"
elif [[ -n "${CODEX_SESSION_ID:-}" ]]; then
    board_session="codex:${CODEX_SESSION_ID}"
else
    board_session="local:${USER:-unknown}:${BASHPID}"
fi

board_id="${PIM_BOARD_ID:-pim}"
lock_command=(
    "$control_bin" board with "$board_id"
    --mode exclusive
    "$lease_flag" "$lease_value"
    --session "$board_session"
    --purpose "$purpose"
)
if [[ "$long_lease" == "true" ]]; then
    lock_command+=(--long-lease true)
fi

exec "${lock_command[@]}" -- env PIM_BOARD_LOCK_HELD=1 "${child[@]}"
```

Mark it executable:

```bash
rtk chmod +x scripts/with_pim_board.sh
```

- [ ] **Step 4: Run focused tests and shell syntax validation**

Run:

```bash
rtk pytest -q tests/test_integration_board_reservation.py
rtk bash -n scripts/with_pim_board.sh
```

Expected: all wrapper tests PASS and `bash -n` exits 0. Confirm the exact fake-control argument list contains the child tail `--`, `env`, `PIM_BOARD_LOCK_HELD=1`, `sh`, `-c`, and the literal test command.

- [ ] **Step 5: Commit the wrapper slice**

```bash
rtk git add scripts/with_pim_board.sh tests/test_integration_board_reservation.py
rtk git commit -m "feat: add fail-fast PIM board wrapper"
```

---

### Task 2: Put long-running local automation under one lease

**Files:**
- Modify: `scripts/auto_chain.sh:7-20`
- Modify: `scripts/auto_overnight.sh:13-24`
- Modify: `scripts/auto_weekend.sh:13-24`
- Modify: `tests/test_integration_board_reservation.py`

**Interfaces:**
- Consumes: `scripts/with_pim_board.sh` from Task 1
- Consumes environment: optional deterministic `PIM_AUTOMATION_TARGET_END` Unix epoch for timed automation reexec/tests
- Produces: self-wrapping `auto_chain.sh` (`--for 24h`) and timed scripts (`--until <ISO-8601>`) with `--long-lease true`

- [ ] **Step 1: Add failing behavioral tests for all three automation scripts**

Add `from datetime import datetime, timedelta, timezone` and
`from typing import Optional` with the imports in
`tests/test_integration_board_reservation.py`, then append these tests:

```python
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
```

- [ ] **Step 2: Run the automation tests to verify the red state**

Run:

```bash
rtk pytest -q tests/test_integration_board_reservation.py -k 'auto or automation'
```

Expected: FAIL because the scripts still hard-code the main checkout and never invoke the wrapper.

- [ ] **Step 3: Add common self-reexec setup to each automation script**

Replace each hard-coded `PROJECT` declaration with:

```bash
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=$(cd -- "$SCRIPT_DIR/.." && pwd)
SCRIPT_PATH="$SCRIPT_DIR/$(basename -- "${BASH_SOURCE[0]}")"
BOARD_WRAPPER="$SCRIPT_DIR/with_pim_board.sh"
```

In `auto_chain.sh`, insert this block before `cd "$PROJECT"` and before `SESSION_TS`:

```bash
if [[ "${PIM_BOARD_LOCK_HELD:-}" != "1" ]]; then
    exec "$BOARD_WRAPPER" \
        --for 24h \
        --purpose "pim-check auto_chain" \
        --long-lease true \
        -- "$SCRIPT_PATH" "$@"
fi
```

Keep the existing `pgrep` loop after acquisition as a migration guard for a legacy unwrapped process. Do not move it ahead of the wrapper and do not replace board-busy exit 4 with its wait behavior.

In `auto_overnight.sh`, move deadline calculation ahead of `cd`, reuse an inherited epoch across reexec, and insert:

```bash
TARGET_END=${PIM_AUTOMATION_TARGET_END:-$(date -d 'tomorrow 09:00' +%s)}
if [[ "${PIM_BOARD_LOCK_HELD:-}" != "1" ]]; then
    export PIM_AUTOMATION_TARGET_END="$TARGET_END"
    TARGET_UNTIL=$(date -d "@$TARGET_END" -Iseconds)
    exec "$BOARD_WRAPPER" \
        --until "$TARGET_UNTIL" \
        --purpose "pim-check auto_overnight" \
        --long-lease true \
        -- "$SCRIPT_PATH" "$@"
fi
```

Delete the old later `TARGET_END=$(date -d 'tomorrow 09:00' +%s)` assignment so there is one deadline source.

In `auto_weekend.sh`, use its existing fallback expression in the same structure:

```bash
TARGET_END=${PIM_AUTOMATION_TARGET_END:-$(date -d 'next monday 09:00' +%s 2>/dev/null || date -d 'monday 09:00' +%s)}
if [[ "${PIM_BOARD_LOCK_HELD:-}" != "1" ]]; then
    export PIM_AUTOMATION_TARGET_END="$TARGET_END"
    TARGET_UNTIL=$(date -d "@$TARGET_END" -Iseconds)
    exec "$BOARD_WRAPPER" \
        --until "$TARGET_UNTIL" \
        --purpose "pim-check auto_weekend" \
        --long-lease true \
        -- "$SCRIPT_PATH" "$@"
fi
```

Delete the old later weekend `TARGET_END` assignment. The wrapper/Project Control error must remain the visible failure when the computed deadline exceeds 72 hours.

- [ ] **Step 4: Run local automation tests and syntax checks**

Run:

```bash
rtk pytest -q tests/test_integration_board_reservation.py
rtk bash -n scripts/with_pim_board.sh scripts/auto_chain.sh scripts/auto_overnight.sh scripts/auto_weekend.sh
```

Expected: all tests PASS; each fake acquisition returns exit 4 before any automation body runs; all four scripts pass `bash -n`.

- [ ] **Step 5: Commit the local automation slice**

```bash
rtk git add scripts/auto_chain.sh scripts/auto_overnight.sh scripts/auto_weekend.sh tests/test_integration_board_reservation.py
rtk git commit -m "fix: reserve PIM board for local plan automation"
```

---

### Task 3: Apply the same lease to all hardware workflows

**Files:**
- Modify: `.github/workflows/hw-verify.yml:95-96`
- Modify: `.github/workflows/hw-verify-comprehensive.yml:64-65`
- Modify: `.github/workflows/hw-verify-plan.yml:100-129`
- Modify: `tests/test_integration_board_reservation.py`

**Interfaces:**
- Consumes: `scripts/with_pim_board.sh` from Task 1
- Produces: CI lease durations `30m`, `3h`, and `12h`; GitHub purpose coordinate `github:<workflow>:<run_id>:<attempt>[:<plan>]`
- Preserves: release-plan child command selection and accepted result codes `0` and `2`

- [ ] **Step 1: Add failing workflow contract tests**

Append the following to `tests/test_integration_board_reservation.py` and add `import yaml` with the imports:

```python
import yaml


def _workflow(path: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / path).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "path, job_name, step_name, lease",
    [
        ("hw-verify.yml", "mixed-combo", "Run mixed_combo verification (4 tests × 10 channel registers)", "30m"),
        ("hw-verify-comprehensive.yml", "comprehensive", "Run comprehensive verification (96 tests, ~2h)", "3h"),
        ("hw-verify-plan.yml", "run-plan", "Run plan", "12h"),
    ],
)
def test_hardware_workflow_uses_common_fail_fast_wrapper(
    path: str, job_name: str, step_name: str, lease: str
) -> None:
    workflow = _workflow(path)
    assert workflow["concurrency"] == {"group": "pim-target-lock", "cancel-in-progress": False}
    step = next(item for item in workflow["jobs"][job_name]["steps"] if item.get("name") == step_name)
    command = step["run"]

    assert command.count("scripts/with_pim_board.sh") == 1
    assert f"--for {lease}" in command
    assert "--purpose" in command
    assert "board wait" not in command
    assert "|| true" not in command


def test_release_plan_wraps_selected_command_and_keeps_warn_policy() -> None:
    workflow = _workflow("hw-verify-plan.yml")
    step = next(item for item in workflow["jobs"]["run-plan"]["steps"] if item.get("name") == "Run plan")
    command = step["run"]

    assert "PLAN_COMMAND=(python3 run_mixed_combo_verify.py)" in command
    assert "PLAN_COMMAND=(python3 run_bps_quick.py)" in command
    assert 'PLAN_COMMAND=(python3 pim_check.py --plan "${{ inputs.plan }}" --host "${{ env.TARGET_HOST }}")' in command
    assert '"${PLAN_COMMAND[@]}"' in command
    assert "if [ $rc -eq 0 ] || [ $rc -eq 2 ]; then" in command
    assert "exit $rc" in command
```

- [ ] **Step 2: Run the workflow tests to verify the red state**

Run:

```bash
rtk pytest -q tests/test_integration_board_reservation.py -k 'workflow or release_plan'
```

Expected: FAIL because all three workflow execution steps still call Python directly.

- [ ] **Step 3: Wrap the mixed and comprehensive execution steps**

Replace the mixed-combo run command with:

```yaml
      - name: Run mixed_combo verification (4 tests × 10 channel registers)
        run: |
          scripts/with_pim_board.sh \
            --for 30m \
            --purpose "github:${{ github.workflow }}:${{ github.run_id }}:${{ github.run_attempt }}" \
            -- python3 run_mixed_combo_verify.py
```

Replace the comprehensive run command with:

```yaml
      - name: Run comprehensive verification (96 tests, ~2h)
        run: |
          scripts/with_pim_board.sh \
            --for 3h \
            --purpose "github:${{ github.workflow }}:${{ github.run_id }}:${{ github.run_attempt }}" \
            -- python3 run_comprehensive_verify.py
```

Do not wrap checkout, installation, target reachability, cache cleanup, result upload, or summary steps.

- [ ] **Step 4: Select one release-plan child command, then invoke the wrapper once**

Replace the body between `set +e` and `rc=$?` in the release-plan run step with:

```bash
          case "${{ inputs.plan }}" in
            mixed_combo)
              PLAN_COMMAND=(python3 run_mixed_combo_verify.py)
              ;;
            bps_quick)
              PLAN_COMMAND=(python3 run_bps_quick.py)
              ;;
            *)
              PLAN_COMMAND=(python3 pim_check.py --plan "${{ inputs.plan }}" --host "${{ env.TARGET_HOST }}")
              ;;
          esac
          scripts/with_pim_board.sh \
            --for 12h \
            --purpose "github:${{ github.workflow }}:${{ github.run_id }}:${{ github.run_attempt }}:${{ inputs.plan }}" \
            -- "${PLAN_COMMAND[@]}"
```

Leave the subsequent `rc=$?`, `$GITHUB_OUTPUT`, `0|2` success mapping, and other exit propagation unchanged. Board-busy exit 4 then follows the existing `else exit $rc` branch.

- [ ] **Step 5: Run workflow contract tests and parse all modified YAML**

Run:

```bash
rtk pytest -q tests/test_integration_board_reservation.py
rtk python3 -c 'import pathlib, yaml; [yaml.safe_load(path.read_text()) for path in pathlib.Path(".github/workflows").glob("hw-verify*.yml")]'
```

Expected: all tests PASS and the YAML parser exits 0.

- [ ] **Step 6: Commit the CI slice**

```bash
rtk git add .github/workflows/hw-verify.yml .github/workflows/hw-verify-comprehensive.yml .github/workflows/hw-verify-plan.yml tests/test_integration_board_reservation.py
rtk git commit -m "fix: serialize hardware CI with local board leases"
```

---

### Task 4: Block known direct commands in Claude sessions

**Files:**
- Create: `scripts/guard_pim_board_command.py`
- Create: `tests/test_guard_pim_board_command.py`
- Create: `.claude/settings.json`
- Modify: `.gitignore:14-22`

**Interfaces:**
- Consumes stdin: Claude `PreToolUse` JSON containing `tool_input.command: str`
- Produces exit `0` for unrelated/wrapped commands and exit `2` plus remediation on stderr for malformed input or direct high-risk commands
- Produces callable functions: `command_is_blocked(command: str) -> bool` and `main() -> int`

- [ ] **Step 1: Write failing guard and project-hook tests**

Create `tests/test_guard_pim_board_command.py`:

```python
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
```

- [ ] **Step 2: Run the guard tests to verify the red state**

Run:

```bash
rtk pytest -q tests/test_guard_pim_board_command.py
```

Expected: FAIL because the guard and project settings do not exist.

- [ ] **Step 3: Implement token-aware, segment-local guard detection**

Create `scripts/guard_pim_board_command.py`:

```python
#!/usr/bin/env python3
"""Block issue #108's direct board-mutating commands in Claude Bash calls."""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import PurePosixPath
from typing import Iterator, Optional


PYTHON = re.compile(r"^python(?:3(?:\.\d+)?)?$")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HARDWARE_RUNNERS = {
    "run_mixed_combo_verify.py",
    "run_comprehensive_verify.py",
    "run_bps_quick.py",
}
SHELL_BREAKS = set(";&|")


def _segments(command: str) -> Iterator[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    segment: list[str] = []
    for token in lexer:
        if token and set(token) <= SHELL_BREAKS:
            if segment:
                yield segment
                segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def _basename(token: str) -> str:
    return PurePosixPath(token).name


def _command_index(tokens: list[str]) -> Optional[int]:
    index = 0
    if tokens and _basename(tokens[0]) == "env":
        index = 1
    while index < len(tokens) and ASSIGNMENT.match(tokens[index]):
        index += 1
    return index if index < len(tokens) else None


def _python_script(tokens: list[str], command_index: int) -> tuple[Optional[str], list[str]]:
    index = command_index + 1
    while index < len(tokens) and tokens[index].startswith("-"):
        if tokens[index] == "-c":
            return None, []
        if tokens[index] == "-m":
            if index + 1 < len(tokens) and tokens[index + 1] == "pim_check":
                return "pim_check.py", tokens[index + 2 :]
            return None, []
        index += 1
    if index >= len(tokens):
        return None, []
    return _basename(tokens[index]), tokens[index + 1 :]


def _segment_is_blocked(tokens: list[str]) -> bool:
    command_index = _command_index(tokens)
    if command_index is None:
        return False
    executable = _basename(tokens[command_index])
    if executable == "with_pim_board.sh":
        return False
    if executable in HARDWARE_RUNNERS:
        return True
    if executable in {"pim_check.py", "pim-check"}:
        return any(arg == "--plan" or arg.startswith("--plan=") for arg in tokens[command_index + 1 :])
    if not PYTHON.match(executable):
        return False
    script, arguments = _python_script(tokens, command_index)
    if script in HARDWARE_RUNNERS:
        return True
    return script == "pim_check.py" and any(
        arg == "--plan" or arg.startswith("--plan=") for arg in arguments
    )


def command_is_blocked(command: str) -> bool:
    try:
        return any(_segment_is_blocked(segment) for segment in _segments(command))
    except ValueError:
        return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError("command must be a string")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"PIM board guard: invalid hook input: {exc}", file=sys.stderr)
        return 2

    if command_is_blocked(command):
        print(
            "PIM board guard: run this command through scripts/with_pim_board.sh "
            "with --for/--until and --purpose; direct board plan execution is blocked.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Mark it executable:

```bash
rtk chmod +x scripts/guard_pim_board_command.py
```

- [ ] **Step 4: Commit the project hook while preserving other local Claude files**

Replace the `.claude/` ignore rule in `.gitignore` with:

```gitignore
.claude/*
!.claude/settings.json
```

Create `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/scripts/guard_pim_board_command.py\""
          }
        ]
      }
    ]
  }
}
```

Run `rtk git status --short .claude/settings.json` and confirm it prints
`?? .claude/settings.json`, proving that the committed exception is effective
without making other `.claude` contents trackable.

- [ ] **Step 5: Run guard tests, lint, and direct hook smoke checks**

Run:

```bash
rtk pytest -q tests/test_guard_pim_board_command.py
rtk ruff check scripts/guard_pim_board_command.py tests/test_guard_pim_board_command.py
rtk python3 -m json.tool .claude/settings.json
rtk proxy bash -lc 'printf "%s" '\''{"tool_name":"Bash","tool_input":{"command":"python3 pim_check.py --plan smoke"}}'\'' | python3 scripts/guard_pim_board_command.py; test $? -eq 2'
```

Expected: pytest and Ruff PASS, JSON renders successfully, and the smoke command observes guard exit 2.

- [ ] **Step 6: Commit the Claude guard slice**

```bash
rtk git add .gitignore .claude/settings.json scripts/guard_pim_board_command.py tests/test_guard_pim_board_command.py
rtk git commit -m "feat: guard direct PIM board commands in Claude"
```

---

### Task 5: Document the canonical invocation and verify the full change

**Files:**
- Modify: `README.md:47,344,354,419`
- Modify: `docs/realtime-monitor-guide.md:36`
- Modify: `AGENTS.md:91`
- Modify: `scripts/AGENTS.md:10-81`
- Modify: `profiles/plans/AGENTS.md:106`
- Modify: `CHANGELOG.md:3`

**Interfaces:**
- Consumes: wrapper CLI, lease table, fail-fast semantics, and advisory limitations from Tasks 1-4
- Produces: one copyable manual invocation pattern and explicit guidance for agents, operators, and future plan authors

- [ ] **Step 1: Replace operational direct-plan examples with bounded wrapper commands**

In README quick start, replace the direct smoke command with:

```bash
scripts/with_pim_board.sh --for 30m --purpose "manual smoke" -- \
  python3 pim_check.py --plan smoke --host 192.168.0.5
```

Replace each operational comprehensive-plan example with the same shape using `--for 3h --purpose "manual comprehensive"`. Leave `--list-plans`, baseline promotion, history, mapping generation, and historical analysis prose unwrapped because they do not run a hardware plan.

In `docs/realtime-monitor-guide.md`, change terminal 1 to:

```bash
scripts/with_pim_board.sh --for 30m --purpose "realtime smoke" -- \
  python3 pim_check.py --plan smoke --host 192.168.214.4 --user root --password root
```

Add one sentence below it: board-busy exit 4 is intentional and the operator should inspect `jhw-control board status pim`, not retry around the lease.

- [ ] **Step 2: Add durable agent and script guidance**

Under the root `AGENTS.md` `<!-- MANUAL: -->` marker, add:

```markdown
## PIM Board Lease

- `pim_check.py --plan` and standalone hardware runners must execute through
  `scripts/with_pim_board.sh` with an explicit `--for`/`--until` and `--purpose`.
- Do not use `board wait`, retry loops, `|| true`, or direct `jhw-control board
  acquire`; a busy board is a visible failure (exit 4).
- Read-only commands such as `--list-plans`, history/report rendering, lint, and
  result comparison do not need the board lease.
- The lease is advisory and issue #108 does not retrofit the persistent web
  dashboard, Docker runner, or non-plan CLI modes.
```

In `scripts/AGENTS.md`, add `with_pim_board.sh`, `guard_pim_board_command.py`, and the three `auto_*.sh` files to the key-file tables. Add the exact wrapper example and the 30m/3h/12h/24h/deadline lease table under `For AI Agents`.

In `profiles/plans/AGENTS.md`, retain the existing lint-first instruction and add that any subsequent real plan execution must use the wrapper; use this exact example:

```bash
scripts/with_pim_board.sh --for 30m --purpose "manual smoke" -- \
  python3 pim_check.py --plan smoke
```

- [ ] **Step 3: Add the issue #108 changelog entry**

At the top of `CHANGELOG.md`'s 2026-08-24 Unreleased section, add a subsection titled `### CI·로컬 PIM 보드 점유 직렬화 (pim-check#108)` that records:

- GitHub `concurrency` did not coordinate with local runs.
- The common exclusive wrapper loads the mode-600 host config and uses the absolute user-local CLI path.
- Busy acquisition fails immediately with exit 4; no waiting or skipping.
- CI leases are 30m/3h/12h; local automation uses 24h or its exact deadline with long-lease opt-in.
- Claude's committed hook is defense in depth, not a security boundary.
- `SIGKILL`/OOM and the excluded persistent/non-plan entry points remain explicit limits.
- Tests use a fake control executable and never acquire the real board.

- [ ] **Step 4: Run focused and full repository verification**

Run:

```bash
rtk pytest -q tests/test_integration_board_reservation.py tests/test_guard_pim_board_command.py
rtk bash -n scripts/with_pim_board.sh scripts/auto_chain.sh scripts/auto_overnight.sh scripts/auto_weekend.sh
rtk python3 -m json.tool .claude/settings.json
rtk ruff check scripts/guard_pim_board_command.py tests/test_guard_pim_board_command.py tests/test_integration_board_reservation.py
rtk pytest -q
rtk git diff --check
```

Expected: focused tests PASS, all shell syntax checks exit 0, JSON parses, Ruff reports no errors, the complete pytest suite passes, and `git diff --check` reports no whitespace errors. Do not run a plan or invoke the real board during verification.

- [ ] **Step 5: Review coverage against the accepted design**

Read the final diff and verify these exact invariants:

```text
3/3 hardware workflows call scripts/with_pim_board.sh exactly once.
3/3 automation scripts self-wrap before SESSION_TS/log creation.
No acquisition path calls board wait or converts exit 4 to success.
hw-verify-plan still accepts only child results 0 and 2.
Only auto_chain/overnight/weekend pass --long-lease true.
README and agent guidance no longer advertise direct operational plan runs.
Tests contain no path to the real $HOME/.local/bin/jhw-control.
```

Expected: every line is evidenced by a test or an inspected diff. If an invariant fails, return to the task that owns it and add a red test before correcting the implementation.

- [ ] **Step 6: Commit documentation and final integration evidence**

```bash
rtk git add README.md docs/realtime-monitor-guide.md AGENTS.md scripts/AGENTS.md profiles/plans/AGENTS.md CHANGELOG.md
rtk git commit -m "docs: require board lease for PIM plan runs"
rtk git status --short
```

Expected: the documentation commit succeeds and `git status --short` is empty.

---

## Push and PR Gate

After all tasks pass and before any push or PR mutation, assert Task ownership with the trusted environment, then follow the repository's PR review gate:

```bash
rtk proxy bash -lc 'set -a; source /home/jhw/.config/jhw-control/control.env; set +a; /home/jhw/.local/bin/jhw-control task assert-owner --task tsk-01a03249-be8c-7759-bd5e-f2fa18918799 --claim clm-01a03249-f551-7a75-b79e-478208360897'
```

Expected: ownership assertion succeeds for branch `task/f2fa18918799-jhw7500-pim-check-108` and this worktree. Stop before push if it does not.
