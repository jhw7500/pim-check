"""Long-lived PIM owner observer: restart, state integrity, and no board IO."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest

from scripts.qa_durable_journal import JournalStore
from scripts.qa_owner_supervisor import (
    OwnerServiceError, OwnerSupervisor, owner_lock, check_source,
    store_status,
)

SHA = "c" * 40
BASELINE = "f" * 64
RUN = "owner-resume-001"


def owner(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("scripts.qa_owner_supervisor.check_source", lambda *_: None)
    return OwnerSupervisor(state, tmp_path, SHA), state


def read_status(state):
    file = state / "owner-status.json"
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    return json.loads(file.read_text())


def test_without_campaign_remains_blocked(tmp_path, monkeypatch):
    supervisor, state = owner(tmp_path, monkeypatch)
    status = supervisor.serve(once=True)
    assert status["reason_code"] == "NO_CAMPAIGN_REGISTERED"
    assert not status["execution_authorized"]
    assert not status["board_touched"]
    assert not status["real_restore_performed"]
    assert read_status(state) == status
    assert supervisor.serve(once=True)["reason_code"] == "NO_CAMPAIGN_REGISTERED"


def test_restart_persists_restore_pending_before_any_device_io(tmp_path, monkeypatch):
    supervisor, state = owner(tmp_path, monkeypatch)
    journal = JournalStore(state)
    row = journal.begin(run_id=RUN, source_sha=SHA, baseline_sha256=BASELINE)
    row = journal.transition("BASELINE_SAVED", expected_revision=row["revision"])
    journal.transition("MUTATION_PENDING", expected_revision=row["revision"])
    status = supervisor.serve(once=True)
    assert status["reason_code"] == "TARGET_RESTORE_REQUIRED"
    assert status["journal_revision"] == 3
    assert journal.read()["state"] == "RESTORE_PENDING"
    assert not status["execution_authorized"]
    assert OwnerSupervisor(state, tmp_path, SHA).serve(once=True)["journal_revision"] == 3


def test_after_simulated_restore_still_requires_independent_attestation(tmp_path, monkeypatch):
    supervisor, state = owner(tmp_path, monkeypatch)
    journal = JournalStore(state)
    row = journal.begin(run_id=RUN, source_sha=SHA, baseline_sha256=BASELINE)
    for next_state in ("BASELINE_SAVED", "MUTATION_PENDING", "RESTORE_PENDING",
                       "FINAL_ATTESTATION_PENDING"):
        row = journal.transition(next_state, expected_revision=row["revision"])
    status = supervisor.serve(once=True)
    assert status["reason_code"] == "FINAL_ATTESTATION_REQUIRED"
    assert not status["execution_authorized"]


def test_corrupt_journal_fails_closed_without_altering_record(tmp_path, monkeypatch):
    supervisor, state = owner(tmp_path, monkeypatch)
    journal = JournalStore(state)
    journal.begin(run_id=RUN, source_sha=SHA, baseline_sha256=BASELINE)
    file = state / "journal.json"
    file.write_bytes(b'{"not":"a valid journal"}')
    before = file.read_bytes()
    status = supervisor.serve(once=True)
    assert status["reason_code"] == "RECOVERY_JOURNAL_UNTRUSTED"
    assert file.read_bytes() == before


def test_source_mismatch_cannot_report_ready(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    s = OwnerSupervisor(state, tmp_path, SHA).serve(once=True)
    assert s["reason_code"] == "DEPLOYED_SOURCE_UNVERIFIED"
    assert s["decision"] == "BLOCK"


@pytest.mark.parametrize("mode", ["unsafe_dir", "symlink_dir"])
def test_insecure_state_directory_rejected(tmp_path, mode):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    if mode == "unsafe_dir":
        state.chmod(0o777)
    else:
        link = tmp_path / "linked-state"
        link.symlink_to(state, target_is_directory=True)
        state = link
    with pytest.raises(OwnerServiceError):
        OwnerSupervisor(state, tmp_path, SHA)


def test_second_owner_is_rejected_without_waiting(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    with owner_lock(state):
        with pytest.raises(OwnerServiceError, match="already running"):
            with owner_lock(state):
                pass


@pytest.mark.parametrize("tamper", ["symlink", "hardlink", "world_readable"])
def test_owner_lock_tampering_rejected(tmp_path, tamper):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    with owner_lock(state):
        pass
    lock = state / ".owner-service.lock"
    other = tmp_path / "outside"
    other.write_bytes(b"must survive")
    if tamper == "symlink":
        lock.unlink()
        lock.symlink_to(other)
    elif tamper == "hardlink":
        os.link(lock, tmp_path / "extra-link")
    else:
        lock.chmod(0o644)
    with pytest.raises(OwnerServiceError):
        with owner_lock(state):
            pass
    assert other.read_bytes() == b"must survive"


def test_status_file_symlink_blocks_overwrite(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    outside = tmp_path / "untouched"
    outside.write_bytes(b"protected")
    (state / "owner-status.json").symlink_to(outside)
    with pytest.raises(OwnerServiceError, match="status"):
        store_status(state, {"decision": "BLOCK"})
    assert outside.read_bytes() == b"protected"


def test_source_pin_rejects_invalid_sha(tmp_path):
    for bad in ("", "invalid", "A" * 40):
        with pytest.raises(OwnerServiceError):
            check_source(tmp_path, bad)


def test_real_os_sigkill_releases_owner_lock_for_restart(tmp_path):
    """Independent Python processes reproduce daemon loss/restart; no board."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    script = (
        "import time;from pathlib import Path;"
        "from scripts.qa_owner_supervisor import owner_lock;"
        f"with owner_lock(Path({str(state)!r})): time.sleep(100)"
    )
    # One-line with statement cannot follow a semicolon; use a newline.
    script = script.replace(";with owner_lock", "\nwith owner_lock")
    child = subprocess.Popen([sys.executable, "-B", "-c", script],
                             cwd=Path(__file__).resolve().parents[1],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        locked = False
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError("child terminated before acquiring lock")
            try:
                with owner_lock(state):
                    pass
            except OwnerServiceError as exc:
                if "already running" in str(exc):
                    locked = True
                    break
            time.sleep(0.02)
        assert locked
    finally:
        child.kill()
        child.wait(timeout=5)
        if child.stderr:
            child.stderr.close()
    with owner_lock(state):
        pass


def test_systemd_unit_stays_passive():
    unit = (Path(__file__).resolve().parents[1] /
            "deploy/pim-qa-owner-observer.service").read_text()
    assert "Type=notify" in unit
    assert "Restart=on-failure" in unit
    assert "WatchdogSec=" in unit
    assert "PrivateNetwork=yes" in unit
    assert "StateDirectoryMode=0700" in unit
    assert "qa_owner_supervisor" in unit
    assert "with_pim_board.sh" not in unit
    assert "pim_check.py --plan" not in unit


def test_unverified_source_does_not_rewrite_pending_intent(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = JournalStore(state)
    row = journal.begin(run_id=RUN, source_sha=SHA, baseline_sha256=BASELINE)
    row = journal.transition("BASELINE_SAVED", expected_revision=row["revision"])
    journal.transition("MUTATION_PENDING", expected_revision=row["revision"])
    result = OwnerSupervisor(state, tmp_path, SHA).serve(once=True)
    assert result["reason_code"] == "DEPLOYED_SOURCE_UNVERIFIED"
    assert journal.read()["state"] == "MUTATION_PENDING"
