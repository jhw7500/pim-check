"""PIM fixed probe parser and read-only journal guard tests (no live SSH)."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import pytest

from scripts.qa_owner_board_adapter import AdapterBlocked
from scripts.qa_owner_board_inspector import PimReadOnlyInspector, REMOTE_PROBE

SHA = "a" * 40
DIGEST = "b" * 64


class FakeSSH:
    def __init__(self, response):
        self.response = response
        self.commands = []

    def run(self, command, *, retries):
        self.commands.append((command, retries))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def valid_sample():
    return {
        "target": "192.168.214.4",
        "observed_at_epoch": 1793448000,
        "journal_state": "ABSENT",
        "journal_phase": None,
        "journal_run_id": None,
        "journal_original_sha": None,
        "edgeconf_sha": DIGEST,
        "ord_conf_sha": DIGEST,
        "runtime_sha": DIGEST,
        "led_flags": [False, False, False, False],
        "binary_hashes": {
            "gstApp": DIGEST, "ord": DIGEST, "vcm": DIGEST,
            "/opt/pim/bin/chk_cam_operate.sh": DIGEST},
        "consumers_ready": True,
        "writer_closed": False,
        "owner_idle": True,
    }


def test_fixed_remote_source_compiles_without_execution():
    compile(REMOTE_PROBE, "<source-owned-read-only-probe>", "exec")


def test_fixed_probe_returns_typed_board_sample():
    f = FakeSSH(json.dumps(valid_sample()))
    data = PimReadOnlyInspector(f, source_sha=SHA).observe()
    assert data.source_sha == SHA
    assert data.target == "192.168.214.4"
    assert data.led_flags == (False,) * 4
    assert data.writer_closed is False
    assert len(f.commands) == 1
    command, retries = f.commands[0]
    assert command.startswith("python3 -B -c ")
    assert retries == 0
    assert "192.168.214.4" in REMOTE_PROBE
    assert "pim-check-recovery" in REMOTE_PROBE
    assert "cam-recoveryctl" in REMOTE_PROBE


@pytest.mark.parametrize("modify", [
    lambda v: v.update({"target": "192.168.0.1"}),
    lambda v: v.update({"led_flags": [False, True, False]}),
    lambda v: v.update({"led_flags": ["false"] * 4}),
    lambda v: v.update({"writer_closed": "yes"}),
    lambda v: v.update({"observed_at_epoch": True}),
    lambda v: v.update({"runtime_sha": None}),
    lambda v: v.update({"unknown": "unsafe"}),
    lambda v: v.pop("journal_run_id"),
])
def test_malformed_readonly_probe_response_is_never_treated_as_valid(modify):
    row = valid_sample()
    modify(row)
    f = FakeSSH(json.dumps(row))
    with pytest.raises(AdapterBlocked):
        PimReadOnlyInspector(f, source_sha=SHA).observe()


@pytest.mark.parametrize("response", [
    None, "{}", "error", "x" * 20000, OSError("board disconnected"),
    RuntimeError("ssh transport disconnected"),
])
def test_transport_and_partial_output_fail_closed(response):
    f = FakeSSH(response)
    with pytest.raises(AdapterBlocked):
        PimReadOnlyInspector(f, source_sha=SHA).observe()


def test_board_probe_has_no_change_or_reboot_operations():
    import scripts.qa_owner_board_inspector as inspector
    assert not hasattr(inspector.PimReadOnlyInspector, "apply_changes")
    assert not hasattr(inspector.PimReadOnlyInspector, "reboot")
    assert not hasattr(inspector.PimReadOnlyInspector, "delete")
    # No generic user string is concatenated into the board program.
    assert "def process_pids()" in REMOTE_PROBE
    assert "def no_writer_handles(pids)" in REMOTE_PROBE
    assert "def journal()" in REMOTE_PROBE
    assert "os.unlink(" not in REMOTE_PROBE
    assert "os.remove(" not in REMOTE_PROBE
    assert "os.rename(" not in REMOTE_PROBE
    assert "os.replace(" not in REMOTE_PROBE


def _journal_only(path):
    """Extract exact real probe functions; inspect offline fake directory.

    No entire REMOTE_PROBE execution; only read_plain, digest, journal.
    This proves absent/unsafe owner conditions, not a root-owned valid
    board journal success (only the real host owner can attest that).
    """
    script = ast.parse(REMOTE_PROBE)
    names = {"read_plain", "digest", "journal"}
    selected = [node for node in script.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    mod = ast.Module(body=selected, type_ignores=[])
    scope = {
        "Path": Path, "ROOT": path,
        "CONFIG": "/root/shared_v/edgeconf_pim.json",
        "MAX_DATA": 4 * 1024 * 1024,
        "os": os, "stat": stat, "hashlib": hashlib, "json": json, "re": re,
    }
    exec(compile(mod, "<read-only-helper-functions>", "exec"), scope)
    return scope["journal"]


def test_no_journal_directory_is_read_only_absent(tmp_path):
    assert _journal_only(tmp_path / "missing")() == (
        "ABSENT", None, None, None)
    assert not (tmp_path / "missing").exists()


def test_owner_mismatch_and_symlink_directory_refuse_clean_journal_claim(tmp_path):
    root = tmp_path / "journal"
    root.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="unsafe recovery directory"):
        _journal_only(root)()
    link = tmp_path / "link"
    link.symlink_to(root)
    with pytest.raises(ValueError, match="unsafe recovery directory"):
        _journal_only(link)()
