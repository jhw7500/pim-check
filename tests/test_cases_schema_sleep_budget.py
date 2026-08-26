"""Schema shell sleeps must fit inside the effective SSH command timeout.

The generated recording checks deliberately wait for files on the target before
running ``find`` and ``ffprobe``.  If a schema wait grows past the SSH command
timeout, healthy boards report timeout failures across the whole check family.
This corpus guard keeps that cross-file budget explicit (pim-check#113).
"""
from __future__ import annotations

import inspect
import pathlib
import re

import pytest
import yaml

from ssh import SshClient


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "profiles"
SCHEMA_PATH = PROFILES_DIR / "schema.yaml"
POST_SLEEP_MARGIN_SECONDS = 60
SLEEP_WORD_RE = re.compile(r"\bsleep\b")
SLEEP_SECONDS_RE = re.compile(r"\bsleep\s+([0-9]+(?:\.[0-9]+)?)\b")


def _values_for_key(value, wanted_key):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == wanted_key:
                yield child
            yield from _values_for_key(child, wanted_key)
    elif isinstance(value, list):
        for child in value:
            yield from _values_for_key(child, wanted_key)


def _schema_sleep_entries():
    schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    entries = []
    unsupported = []

    for command in _values_for_key(schema, "command"):
        assert isinstance(command, str), f"schema command must be a string: {command!r}"
        parsed = [float(value) for value in SLEEP_SECONDS_RE.findall(command)]
        if len(parsed) != len(SLEEP_WORD_RE.findall(command)):
            unsupported.append(command)
        entries.extend((seconds, command) for seconds in parsed)

    assert not unsupported, (
        "unsupported schema sleep syntax; extend the parser instead of ignoring it:\n"
        + "\n".join(unsupported)
    )
    assert entries, "schema contains no parsed sleep commands; timeout guard is vacuous"
    return entries


def _profile_command_timeout_overrides():
    paths = [PROFILES_DIR / "base.yaml", *sorted((PROFILES_DIR / "cases").glob("*.yaml"))]
    overrides = []

    for path in paths:
        profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for value in _values_for_key(profile, "command_timeout"):
            assert isinstance(value, (int, float)) and not isinstance(value, bool), (
                f"{path.relative_to(ROOT)} command_timeout must be numeric: {value!r}"
            )
            overrides.append((float(value), str(path.relative_to(ROOT))))

    return overrides


def _effective_command_timeout():
    default = inspect.signature(SshClient.__init__).parameters["command_timeout"].default
    assert isinstance(default, (int, float)) and not isinstance(default, bool), (
        f"SshClient command_timeout default must be numeric: {default!r}"
    )
    candidates = [(float(default), "SshClient default"), *_profile_command_timeout_overrides()]
    return min(candidates, key=lambda item: item[0])


def _assert_sleep_budget(entries, timeout, timeout_source):
    max_sleep = max(seconds for seconds, _command in entries)
    required = max_sleep + POST_SLEEP_MARGIN_SECONDS
    longest_commands = sorted(
        {command for seconds, command in entries if seconds == max_sleep}
    )

    assert required <= timeout, (
        f"schema sleep budget exceeded: max sleep {max_sleep:g}s + "
        f"{POST_SLEEP_MARGIN_SECONDS}s post-sleep margin = {required:g}s, "
        f"but {timeout_source} command_timeout is {timeout:g}s; "
        "longest command(s):\n" + "\n".join(longest_commands)
    )


def test_schema_sleep_budget_fits_effective_ssh_command_timeout():
    """Raising a schema sleep or lowering an effective timeout must fail loudly."""
    entries = _schema_sleep_entries()
    max_sleep = max(seconds for seconds, _command in entries)
    timeout, timeout_source = _effective_command_timeout()

    # Mutation proof: one second below the required budget must be rejected.
    with pytest.raises(AssertionError, match="schema sleep budget exceeded"):
        _assert_sleep_budget(
            entries,
            max_sleep + POST_SLEEP_MARGIN_SECONDS - 1,
            "mutation check",
        )

    _assert_sleep_budget(entries, timeout, timeout_source)
