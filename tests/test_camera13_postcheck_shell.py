"""Run the actual postcheck shell command against synthetic camera13 state."""
import copy
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import yaml


COMMAND_FILE = Path(__file__).resolve().parents[1] / "profiles/cases/camera_reset_postcheck.yaml"


def test_postcheck_shell_valid_and_invalid_histories():
    if not shutil.which("jq"):
        pytest.skip("jq is required to exercise the actual shell predicate")
    profile = yaml.safe_load(COMMAND_FILE.read_text())
    shell = profile["checks"]["custom_commands"][-1]["command"]
    request_id = "01234567-89ab-cdef-0123-456789abcdef"
    owner = {
        "boot_id": "boot1", "invocation_id": "inv1", "pid": 88,
        "proc_start_time": "102", "lifecycle": "ACTIVE",
    }
    marker = {
        "request_id": request_id, "type": "apply_config",
        "checked_at": int(time.time()),
        "owner": {k: owner[k] for k in (
            "boot_id", "invocation_id", "pid", "proc_start_time")},
    }
    history = {
        "request": {
            "id": request_id, "type": "apply_config",
            "source": "pim-check", "status": "SUCCEEDED", "rc": 0,
        },
        "actions": [{
            "action": "camera_hard_reset", "request_id": request_id,
            "status": "SUCCEEDED", "rc": 0,
        }],
    }

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "history").mkdir()
        marker_path = root / "audit.json"
        owner_path = root / "owner.json"
        history_path = root / "history" / f"{request_id}.json"
        command = shell.replace(
            "/tmp/pim_check_recovery_audit.json", str(marker_path),
        ).replace(
            "/run/pim-camera/owner.json", str(owner_path),
        ).replace(
            "/var/lib/pim-camera/recovery/history/", str(root / "history") + "/",
        )

        def run(current_owner, current_history):
            marker_path.write_text(json.dumps(marker))
            owner_path.write_text(json.dumps(current_owner))
            history_path.write_text(json.dumps(current_history))
            result = subprocess.run(
                ["sh", "-c", command], capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        assert run(owner, history) == "VERIFIED"
        assert run({**owner, "invocation_id": "wrong"}, history) == "INVALID"
        without_reset = copy.deepcopy(history)
        without_reset["actions"] = []
        assert run(owner, without_reset) == "INVALID"
        with_fallback = copy.deepcopy(history)
        with_fallback["actions"].append({"action": "reboot_fallback"})
        assert run(owner, with_fallback) == "INVALID"
        wrong_request = copy.deepcopy(history)
        wrong_request["request"]["id"] = "not-the-request"
        assert run(owner, wrong_request) == "INVALID"
