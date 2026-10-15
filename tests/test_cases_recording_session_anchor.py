"""Guard recording selectors against choosing a prior reset session's files."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


CASES_DIR = Path(__file__).resolve().parent.parent / "profiles" / "cases"
ANCHOR_ASSIGNMENT = (
    "ANCHOR=$(awk -v i=\"$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)\" "
    "'NR==1{a=$0} NR==2 && $0==i{print a}' /tmp/pim_check_anchor 2>/dev/null)"
)
ANCHOR_FAILURE = '[ -z "$ANCHOR" ] && echo FAIL:NO_SESSION_ANCHOR && exit 0'
FIND_PREFIX = 'FILES=$(find /dev/shm/recordings /mnt/sd_cam -newermt "$ANCHOR" -name '
REQUIRED_PREFIX = f"{ANCHOR_ASSIGNMENT}\n{ANCHOR_FAILURE}\n{FIND_PREFIX}"


def _recording_commands() -> list[tuple[str, str, str]]:
    """Return every multi-case custom command that selects a recording."""
    commands = []
    for path in sorted(CASES_DIR.glob("multi_*.yaml")):
        profile = yaml.safe_load(path.read_text()) or {}
        for check in (profile.get("checks") or {}).get("custom_commands") or []:
            command = check.get("command", "")
            if "find /dev/shm/recordings" in command:
                commands.append((path.name, check.get("name", ""), command))
    return commands


def test_recording_selectors_have_the_exact_fail_closed_session_boundary():
    """Stale files from either reset session cannot satisfy an ordered file gate."""
    commands = _recording_commands()
    assert len({filename for filename, _, _ in commands}) == 16
    assert len(commands) == 108

    failures = []
    for filename, name, command in commands:
        missing = []
        if not command.startswith(REQUIRED_PREFIX):
            missing.append("ordered fail-closed anchor/find prefix")
        if "uptime -s" in command:
            missing.append("boot-time fallback in strict selector")
        if missing:
            failures.append(f"{filename}: {name} — {', '.join(missing)}")

    assert not failures, "unscoped recording selectors:\n" + "\n".join(failures)


def test_recording_selector_commands_parse_without_execution():
    """All 108 remote shell snippets remain parseable without touching a target."""
    failures = []
    for filename, name, command in _recording_commands():
        result = subprocess.run(
            ["/bin/sh", "-n", "-c", command],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            failures.append(
                f"{filename}: {name} — {result.stderr.strip() or result.stdout.strip()}"
            )

    assert not failures, "recording selector shell syntax errors:\n" + "\n".join(failures)
