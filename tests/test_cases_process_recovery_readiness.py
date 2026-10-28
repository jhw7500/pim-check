"""Exercise the production recovery predicate against an isolated fake board.

PIDs can appear before camera health and listeners. Verification must keep its
bounded grace period available and preserve markers when recovery is unknown.
The verifier and mocked recovery control run only against temporary fixtures;
the destructive injection is never executed.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from config import load_profile

PROFILES = str(Path(__file__).resolve().parents[1] / "profiles")


def _run_verifier(tmp_path, *, health_after=0, ports_after=0, zombie=False, stale=False,
                  missing_port=None):
    marker = tmp_path / "before"
    state = tmp_path / "state"
    health = tmp_path / "health"
    ticks = tmp_path / "ticks"
    marker.write_text("1 2 3\n")
    state.write_text("STALE_MARKER\n" if stale else "INJECT_VERIFIED\n")
    health.write_text("healthy\n" if health_after == 0 else "starting\n")
    ticks.write_text("0\n")
    proc_root = tmp_path / "fake-proc"
    for pid in ("101", "102", "103"):
        directory = proc_root / pid
        directory.mkdir(parents=True)
        status = "Z" if zombie and pid == "103" else "S"
        (directory / "stat").write_text(f"{pid} (mock) {status} 0\n")

    command = load_profile(PROFILES, case="process_restart_smoke")["teardown"]["recovery_verify_command"]
    replacements = {
        "/tmp/pim_restart_smoke_before": str(marker),
        "/tmp/pim_restart_smoke_state": str(state),
        "/tmp/cam_state/state": str(health),
        "/root/shared_v/ord_vcm_conf.json": str(tmp_path / "config.json"),
        "/proc/": str(proc_root) + "/",
    }
    for original, local in replacements.items():
        command = command.replace(original, local)
    assert all(original not in command for original in replacements)
    prelude = f"""
TICKS={shlex.quote(str(ticks))}
HEALTH={shlex.quote(str(health))}
CONFIG={shlex.quote(str(tmp_path / "config.json"))}
pgrep() {{
  case "$2" in gstApp) echo 101;; ord) echo 102;; vcm) echo 103;; *) return 1;; esac
}}
jq() {{
  [ "$#" -eq 3 ] && [ "$3" = "$CONFIG" ] || return 97
  case "$*" in *ORD.port_num*) echo 10007;; *VCM.port_num*) echo 10009;; *) return 1;; esac
}}
ss() {{
  N=$(cat "$TICKS")
  if [ "$N" -ge {ports_after} ]; then
    [ '{missing_port}' = ord ] || printf 'LISTEN 0 128 0.0.0.0:10007 0.0.0.0:*\\n'
    [ '{missing_port}' = vcm ] || printf 'LISTEN 0 128 0.0.0.0:10009 0.0.0.0:*\\n'
  fi
}}
sleep() {{
  N=$(cat "$TICKS"); N=$((N + 1)); printf '%s\\n' "$N" > "$TICKS"
  if [ "$N" -ge {health_after} ]; then printf 'healthy\\n' > "$HEALTH"; fi
  return 0
}}
"""
    result = subprocess.run(["/bin/sh", "-c", prelude + command],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip(), marker, state, int(ticks.read_text())


@pytest.mark.parametrize("health_after,ports_after", [(0, 0), (2, 0), (0, 2), (2, 3)])
def test_waits_until_process_camera_and_listener_postconditions_are_all_ready(
        tmp_path, health_after, ports_after):
    output, marker, state, ticks = _run_verifier(
        tmp_path, health_after=health_after, ports_after=ports_after)
    assert output == "OK"
    assert ticks == max(health_after, ports_after)
    assert not marker.exists() and not state.exists()


@pytest.mark.parametrize("problem", ["health", "ports", "ord-port", "vcm-port", "zombie", "stale"])
def test_unverified_recovery_preserves_markers_and_has_a_bounded_wait(tmp_path, problem):
    output, marker, state, ticks = _run_verifier(
        tmp_path, health_after=100 if problem == "health" else 0,
        ports_after=100 if problem == "ports" else 0,
        zombie=problem == "zombie", stale=problem == "stale",
        missing_port=problem.split("-")[0] if problem.endswith("-port") else None)
    assert output == "FAIL"
    assert marker.exists() and state.exists()
    assert 0 <= ticks <= 6
    if problem == "stale":
        assert ticks == 0
        assert state.read_text() == "STALE_MARKER\n"


def _run_recovery_command(tmp_path, marker_state):
    marker = tmp_path / "before"
    state = tmp_path / "state"
    calls = tmp_path / "recovery-args"
    marker.write_text("1 2 3\n")
    if marker_state is not None:
        state.write_text(marker_state + "\n")
    control = tmp_path / "recoveryctl"
    control.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(calls)) + "\n")
    control.chmod(0o700)
    command = load_profile(PROFILES, case="process_restart_smoke")["teardown"]["recovery_command"]
    for old, new in {
        "/opt/pim/bin/cam-recoveryctl": str(control),
        "/tmp/pim_restart_smoke_before": str(marker),
        "/tmp/pim_restart_smoke_state": str(state),
    }.items():
        command = command.replace(old, new)
    assert "/opt/pim/" not in command
    command = "pgrep() { return 1; }; sleep() { return 0; };\n" + command
    result = subprocess.run(["/bin/sh", "-c", command],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    return calls, marker, state


@pytest.mark.parametrize("marker_state", ["INJECT_VERIFIED", "AUTO_RECOVERY_FAILED"])
def test_official_recovery_receives_all_required_arguments_once(tmp_path, marker_state):
    calls, marker, state = _run_recovery_command(tmp_path, marker_state)
    assert calls.read_text().splitlines() == [
        "request", "gstapp_restart", "--source", "pim-check",
        "--reason", "process-restart-smoke-cleanup", "--wait", "120",
    ]
    assert marker.exists() and state.exists()


@pytest.mark.parametrize("marker_state", [
    "STALE_MARKER", "PRECONDITION_FAILED", "ANCHOR_FAILED", None,
])
def test_unowned_recovery_marker_never_submits_a_recovery_action(tmp_path, marker_state):
    calls, marker, state = _run_recovery_command(tmp_path, marker_state)
    assert not calls.exists()
    assert marker.exists()
    if marker_state is not None:
        assert state.read_text().strip() == marker_state
