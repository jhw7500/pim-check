"""Real shell/profile regressions for read-only ORD/VCM QA; never contact a board."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from checks.custom import CustomCommandCheck
from config import load_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILES = str(ROOT / "profiles")
JQ = shutil.which("jq")


def specs(case):
    return load_profile(PROFILES, case=case)["checks"]["custom_commands"]


def spec(case, text):
    return next(x for x in specs(case) if text in x["name"])


def passes(item, observed):
    ssh = MagicMock()
    ssh.run.return_value = observed
    check = CustomCommandCheck()
    return check.validate(check.collect(ssh, {"custom_commands": [item]}), {})[0]


@pytest.mark.parametrize("case", ["verify_ord_operation", "verify_vcm_operation"])
def test_standalone_sanity_is_explicitly_readonly_and_snapshot(case):
    profile = load_profile(PROFILES, case=case)
    assert not profile.get("setup") and not profile.get("teardown")
    assert "read_only" in profile.get("tags", [])
    assert profile["monitor"]["duration_sec"] == 0


@pytest.mark.parametrize("case,family", [("verify_ord_operation", "ORD"), ("verify_vcm_operation", "VCM")])
def test_listener_and_connect_use_validated_runtime_port(case, family):
    for text in ("리슨", "TCP 연결"):
        command = spec(case, text)["command"]
        assert f".{family}.port_num" in command
        assert "floor" in command and "65535" in command
        assert "10007" not in command and "10009" not in command


@pytest.mark.parametrize("value,expected", [("true", True), ("false", True), ("null", False), ('"true"', False), ("garbage", False), ("", False), (None, False)])
def test_srt_enabled_is_valid_and_missing_is_not(value, expected):
    assert passes(spec("verify_vcm_operation", "srt_enable"), value) is expected


def test_disk_percentage_above_100_must_not_pass():
    item = spec("verify_ord_operation", "disk_limit_per")
    assert passes(item, "80") and passes(item, "100")
    assert not passes(item, "101")


@pytest.mark.parametrize("running", [True, False])
def test_real_shell_pid_liveness_does_not_include_literal_quote_characters(tmp_path, running):
    pid = os.getpid() if running else 2147483647
    executable = tmp_path / "pgrep"
    executable.write_text(f"#!/bin/sh\nprintf '%s\\n' '{pid}'\n")
    executable.chmod(0o755)
    command = spec("verify_ord_operation", "PID stable")["command"]
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")}
    result = subprocess.run(["/bin/sh", "-c", command], env=env, capture_output=True, text=True, timeout=6)
    assert result.returncode == 0
    assert result.stdout.strip() == ("STABLE" if running else "UNSTABLE")


def shell(item, tmp_path, config, edge=None, listener_port=24123):
    config_file = tmp_path / "ord_vcm.json"
    config_file.write_text(json.dumps(config))
    edge_file = tmp_path / "edgeconf.json"
    edge_file.write_text(json.dumps(edge or {}))
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    ss = bindir / "ss"
    ss.write_text(f"#!/bin/sh\nprintf '%s\\n' 'LISTEN 0 128 127.0.0.1:{listener_port} 0.0.0.0:*'\n")
    ss.chmod(0o755)
    timeout = bindir / "timeout"
    timeout.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$TRACE\"\nexit 0\n")
    timeout.chmod(0o755)
    command = item["command"].replace('/root/shared_v/ord_vcm_conf.json', shlex.quote(str(config_file)))
    command = command.replace('/root/shared_v/edgeconf_pim.json', shlex.quote(str(edge_file)))
    assert '/root/shared_v/' not in command
    env = {**os.environ, "TRACE": str(tmp_path / "trace"), "PATH": str(bindir) + os.pathsep + os.path.dirname(JQ) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")}
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, env=env, timeout=5)
    assert result.returncode == 0
    return result.stdout.strip()


@pytest.mark.skipif(JQ is None, reason="actual jq required for shell configuration validation")
@pytest.mark.parametrize("case,family", [("verify_ord_operation", "ORD"), ("verify_vcm_operation", "VCM")])
@pytest.mark.parametrize("port", [0, -1, 65536, 1.5, None, True, "123;echo BAD"])
def test_invalid_configured_port_never_probes_network(tmp_path, case, family, port):
    for text in ("리슨", "TCP 연결"):
        output = shell(spec(case, text), tmp_path, {family: {"port_num": port}})
        assert output == "INVALID_PORT"
        assert not (tmp_path / "trace").exists()


@pytest.mark.skipif(JQ is None, reason="actual jq required for shell configuration validation")
@pytest.mark.parametrize("case,family", [("verify_ord_operation", "ORD"), ("verify_vcm_operation", "VCM")])
def test_nondefault_runtime_port_is_used(tmp_path, case, family):
    config = {family: {"port_num": 24123}}
    assert shell(spec(case, "리슨"), tmp_path, config) == "1"
    assert shell(spec(case, "TCP 연결"), tmp_path, config) == "OK"
    assert "127.0.0.1/24123" in (tmp_path / "trace").read_text()
    assert shell(spec(case, "리슨"), tmp_path, config, listener_port=124123) == "0"


@pytest.mark.skipif(JQ is None, reason="actual jq required for shell configuration validation")
@pytest.mark.parametrize("case", ["verify_ord_operation", "verify_ord_vcm_integration"])
@pytest.mark.parametrize("left,right,expected", [
    (True, True, "MATCH"),
    (False, False, "MATCH"),
    (True, False, "MISMATCH"),
    (None, None, "MISMATCH"),
    ("true", "true", "MISMATCH"),
    (1, 1, "MISMATCH"),
    ([], [], "MISMATCH"),
    ({}, {}, "MISMATCH"),
])
def test_event_policy_requires_matching_booleans(tmp_path, case, left, right, expected):
    item = spec(case, "event_auto_remove")
    result = shell(item, tmp_path, {"ORD": {"event_auto_remove": right}}, {"VHL_CAM": {"event_auto_remove": left}})
    assert result == expected
    assert passes(item, result) is (expected == "MATCH")


@pytest.mark.skipif(JQ is None, reason="actual jq required for shell configuration validation")
@pytest.mark.parametrize("case", ["verify_ord_operation", "verify_ord_vcm_integration"])
@pytest.mark.parametrize("camera,ord", [
    ({}, {}),
    ({}, {"event_auto_remove": True}),
    ({"event_auto_remove": True}, {}),
])
def test_event_policy_missing_field_is_not_matching(tmp_path, case, camera, ord):
    item = spec(case, "event_auto_remove")
    result = shell(item, tmp_path, {"ORD": ord}, {"VHL_CAM": camera})
    assert result == "MISMATCH"
    assert not passes(item, result)
