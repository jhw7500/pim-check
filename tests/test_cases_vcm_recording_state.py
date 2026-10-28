"""A recent directory must not impersonate an updated VCM state file."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from checks.custom import CustomCommandCheck
from config import load_profile

PROFILES = str(Path(__file__).resolve().parents[1] / "profiles")


@pytest.mark.parametrize("kind,expected_count", [
    ("empty-directory", 0), ("nested-directory", 0), ("old-file", 0),
    ("fresh-file", 1), ("missing-directory", 0),
])
def test_recent_recording_state_counts_only_regular_files(tmp_path, kind, expected_count):
    directory = tmp_path / "recording"
    if kind != "missing-directory":
        directory.mkdir()
    if kind == "nested-directory":
        (directory / "nested").mkdir()
    if kind in {"fresh-file", "old-file"}:
        state = directory / "ch0"
        state.write_text("recording\n")
        if kind == "old-file":
            before = time.time() - 600
            os.utime(state, (before, before))
    profile = load_profile(PROFILES, case="verify_vcm_operation")
    spec = next(item for item in profile["checks"]["custom_commands"]
                if item["name"] == "recording 상태 파일 최근 갱신")
    command = spec["command"].replace("/tmp/cam_state/recording/", str(directory))
    assert "/tmp/cam_state" not in command
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True,
                            text=True, timeout=5)
    assert result.returncode == 0
    assert int(result.stdout.strip()) == expected_count
    ssh = MagicMock()
    ssh.run.return_value = result.stdout
    check = CustomCommandCheck()
    collected = check.collect(ssh, {"custom_commands": [spec]})
    assert check.validate(collected, {})[0] is (kind == "fresh-file")
