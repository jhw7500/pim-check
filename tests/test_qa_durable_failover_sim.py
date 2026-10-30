"""Offline failover simulation tests: real local files and subprocess death."""
from __future__ import annotations

from pathlib import Path
import os
import signal
import subprocess
import sys
import threading

import pytest

from scripts.qa_durable_journal import JournalError, JournalStore
from scripts.qa_durable_failover_sim import (
    OfflineCoordinator,
    SimulatedDevice,
    SimulationError,
)

SOURCE_SHA = "a" * 40
OTHER_SHA = "b" * 40
RUN_ID = "qa-owner-failover-01"
BASELINE = b"FAKE_CAMERA_ORIGINAL_CONFIG=unchanged\n"
MUTATED = b"SIMULATED_FAULT_ONLY\n"


def prepared(tmp_path):
    device_dir = tmp_path / "simulated-device"
    target = SimulatedDevice(device_dir)
    target.initialize(BASELINE)
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(mode=0o700)
    coordinator = OfflineCoordinator(
        journal_dir=journal_dir, device_dir=device_dir, source_sha=SOURCE_SHA)
    state = coordinator.prepare(run_id=RUN_ID)
    assert state["state"] == "PREPARED"
    assert state["decision"] == "BLOCK"
    assert state["execution_authorized"] is False
    return coordinator, target, journal_dir, device_dir


def assert_unapproved(result):
    assert result["decision"] == "BLOCK"
    assert result["execution_authorized"] is False
    assert result["evidence_only"] is True


def test_killed_controller_after_mutation_recovers_on_new_process(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    code = (
        "import os,signal;"
        "from pathlib import Path;"
        "from scripts.qa_durable_failover_sim import OfflineCoordinator;"
        "owner=OfflineCoordinator("
        f"journal_dir=Path({str(journal_dir)!r}),"
        f"device_dir=Path({str(device_dir)!r}),source_sha={SOURCE_SHA!r});"
        "owner.simulate_mutation();"
        "os.kill(os.getpid(),signal.SIGKILL)"
    )
    child = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=Path(__file__).resolve().parents[1], timeout=20, check=False,
    )
    assert child.returncode == -signal.SIGKILL
    assert target.read_current() == MUTATED
    assert JournalStore(journal_dir).read()["state"] == "MUTATION_PENDING"
    resumed = OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA)
    recovered = resumed.recover()
    assert target.read_current() == BASELINE
    assert recovered["state"] == "FINAL_ATTESTATION_PENDING"
    assert recovered["action"] == "INDEPENDENT_FINAL_ATTESTATION_REQUIRED"
    assert recovered["revision"] == 4
    assert_unapproved(recovered)


def test_owner_restart_after_restore_before_final_is_idempotent(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    coordinator.simulate_mutation()
    assert target.read_current() == MUTATED
    code = (
        "import os;"
        "from pathlib import Path;"
        "from scripts.qa_durable_journal import JournalStore;"
        "from scripts.qa_durable_failover_sim import SimulatedDevice;"
        f"JournalStore(Path({str(journal_dir)!r})).transition("
        "'RESTORE_PENDING', expected_revision=2);"
        f"SimulatedDevice(Path({str(device_dir)!r})).restore();"
        "os._exit(77)"
    )
    child = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=Path(__file__).resolve().parents[1], timeout=20, check=False,
    )
    assert child.returncode == 77
    assert target.read_current() == BASELINE
    assert JournalStore(journal_dir).read()["state"] == "RESTORE_PENDING"
    recovered = OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA).recover()
    assert recovered["state"] == "FINAL_ATTESTATION_PENDING"
    assert target.read_current() == BASELINE
    assert_unapproved(recovered)


def test_failed_restore_keeps_journal_pending_for_retry(tmp_path, monkeypatch):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    coordinator.simulate_mutation()
    original_restore = SimulatedDevice.restore

    def broken_restore(self):
        raise OSError("synthetic disk restore failure")

    monkeypatch.setattr(SimulatedDevice, "restore", broken_restore)
    with pytest.raises(SimulationError, match="restore failed"):
        coordinator.recover()
    assert JournalStore(journal_dir).read()["state"] == "RESTORE_PENDING"
    assert target.read_current() == MUTATED

    monkeypatch.setattr(SimulatedDevice, "restore", original_restore)
    recovered = OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA).recover()
    assert target.read_current() == BASELINE
    assert recovered["state"] == "FINAL_ATTESTATION_PENDING"
    assert_unapproved(recovered)


def test_abort_before_mutation_requires_unchanged_fixture(tmp_path):
    coordinator, target, journal_dir, _ = prepared(tmp_path)
    result = coordinator.recover()
    assert result["action"] == "ABORT_WITHOUT_MUTATION"
    assert result["state"] == "PREPARED"
    assert target.read_current() == BASELINE
    assert_unapproved(result)
    assert JournalStore(journal_dir).read()["revision"] == 0


def test_unjournaled_change_cannot_be_silently_restored(tmp_path):
    coordinator, target, journal_dir, _ = prepared(tmp_path)
    target.simulate_fault()
    with pytest.raises(SimulationError, match="unexpected"):
        coordinator.recover()
    assert target.read_current() == MUTATED
    assert JournalStore(journal_dir).read()["state"] == "PREPARED"


def test_wrong_source_pin_does_not_restore_or_start_another_run(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    coordinator.simulate_mutation()
    mismatched = OfflineCoordinator(journal_dir, device_dir, OTHER_SHA)
    with pytest.raises(JournalError, match="source"):
        mismatched.recover()
    assert target.read_current() == MUTATED
    assert JournalStore(journal_dir).read()["state"] == "MUTATION_PENDING"


def test_baseline_tamper_blocks_restore_without_lying(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    coordinator.simulate_mutation()
    (device_dir / "baseline.bin").write_bytes(b"forged fixture")
    with pytest.raises(SimulationError, match="baseline"):
        coordinator.recover()
    assert target.read_current() == MUTATED
    assert JournalStore(journal_dir).read()["state"] == "MUTATION_PENDING"


def test_second_campaign_rejected_after_prepare(tmp_path):
    coordinator, target, journal_dir, _ = prepared(tmp_path)
    with pytest.raises(JournalError, match="already"):
        coordinator.prepare(run_id="qa-another-run")
    assert JournalStore(journal_dir).read()["run_id"] == RUN_ID
    assert target.read_current() == BASELINE


def test_missing_journal_does_not_justify_restoration(tmp_path):
    coordinator, target, journal_dir, _ = prepared(tmp_path)
    (journal_dir / "journal.json").unlink()
    with pytest.raises(JournalError, match="missing"):
        coordinator.recover()
    assert target.read_current() == BASELINE


def test_restored_fixture_still_needs_independent_attestation(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    coordinator.simulate_mutation()
    first = coordinator.recover()
    second = OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA).recover()
    assert first["state"] == second["state"] == "FINAL_ATTESTATION_PENDING"
    assert second["action"] == "INDEPENDENT_FINAL_ATTESTATION_REQUIRED"
    assert second["revision"] == first["revision"]
    assert_unapproved(second)


def test_offline_simulator_rejects_non_temp_or_wrong_directory(tmp_path):
    with pytest.raises(ValueError):
        SimulatedDevice(tmp_path / "actual-device")
    with pytest.raises(ValueError):
        SimulatedDevice(Path("/mnt/sd_cam/simulated-device"))
    assert list(tmp_path.iterdir()) == []


def test_offline_fixture_no_network_or_runtime_activation(tmp_path):
    coordinator, target, _, _ = prepared(tmp_path)
    assert not hasattr(coordinator, "run_board")
    assert not hasattr(coordinator, "install_service")
    assert not hasattr(coordinator, "authorize_execution")
    assert not hasattr(target, "ssh")
    status = coordinator.simulate_mutation()
    assert status["state"] == "MUTATION_PENDING"
    assert_unapproved(status)
    assert target.read_current() == MUTATED



def test_failed_simulated_mutation_keeps_intent_and_recovery_is_idempotent(
    tmp_path, monkeypatch,
):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)

    def broken_mutation(self):
        raise OSError("synthetic local device write refused")

    monkeypatch.setattr(SimulatedDevice, "simulate_fault", broken_mutation)
    with pytest.raises(OSError, match="synthetic"):
        coordinator.simulate_mutation()
    # The journal must have been fsynced BEFORE attempting local mutation.
    assert JournalStore(journal_dir).read()["state"] == "MUTATION_PENDING"
    assert target.read_current() == BASELINE
    result = OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA).recover()
    assert result["state"] == "FINAL_ATTESTATION_PENDING"
    assert_unapproved(result)


def test_corrupt_journal_after_mutation_does_not_trigger_untrusted_restore(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    coordinator.simulate_mutation()
    file = journal_dir / "journal.json"
    raw = file.read_bytes()
    file.write_bytes(raw.replace(b"MUTATION_PENDING", b"RESTORE_PENDING"))
    assert target.read_current() == MUTATED
    with pytest.raises(JournalError, match="integrity"):
        OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA).recover()
    assert target.read_current() == MUTATED


def test_symlinked_fake_baseline_cannot_escape_the_temporary_fixture(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"protected external file")
    (device_dir / "baseline.bin").unlink()
    (device_dir / "baseline.bin").symlink_to(outside)
    with pytest.raises(SimulationError):
        coordinator.simulate_mutation()
    assert outside.read_bytes() == b"protected external file"
    assert target.read_current() == BASELINE
    assert JournalStore(journal_dir).read()["state"] == "PREPARED"


@pytest.mark.parametrize("tamper", ["symlink", "hardlink", "missing", "world_readable"])
def test_restore_refuses_tampered_simulated_current_without_claiming_success(
    tmp_path, tamper,
):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    coordinator.simulate_mutation()
    current = device_dir / "current.bin"
    external = tmp_path / "external-not-to-overwrite"
    external.write_bytes(MUTATED)
    if tamper == "symlink":
        current.unlink()
        current.symlink_to(external)
    elif tamper == "hardlink":
        current.unlink()
        os.link(external, current)
    elif tamper == "missing":
        current.unlink()
    else:
        current.chmod(0o644)

    with pytest.raises(SimulationError, match="simulation|fixture|restore"):
        coordinator.recover()

    assert JournalStore(journal_dir).read()["state"] == "RESTORE_PENDING"
    assert external.read_bytes() == MUTATED
    assert (device_dir / "baseline.bin").read_bytes() == BASELINE


def test_crash_after_intent_before_target_effect_is_safely_recovered(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    journal = JournalStore(journal_dir)
    journal.transition("BASELINE_SAVED", expected_revision=0)
    journal.transition("MUTATION_PENDING", expected_revision=1)
    # Simulates SIGKILL between a durable intent and the first external write.
    assert target.read_current() == BASELINE
    result = OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA).recover()
    assert target.read_current() == BASELINE
    assert result["state"] == "FINAL_ATTESTATION_PENDING"
    assert_unapproved(result)


def test_second_owner_cannot_inject_another_fault_during_pending_restore(tmp_path):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    coordinator.simulate_mutation()
    second = OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA)
    with pytest.raises(SimulationError, match="unresolved"):
        second.simulate_mutation()
    assert JournalStore(journal_dir).read()["state"] == "MUTATION_PENDING"
    assert target.read_current() == MUTATED


def test_inflight_fault_cannot_run_after_concurrent_recovery(tmp_path, monkeypatch):
    """A new owner must not restore ahead of the old owner's pending side effect."""
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    mutation_reached = threading.Event()
    release_mutation = threading.Event()
    restore_started = threading.Event()
    failures = []
    original_fault = coordinator.device.simulate_fault
    original_restore = SimulatedDevice.restore

    def paused_fault():
        mutation_reached.set()
        if not release_mutation.wait(timeout=8):
            raise AssertionError("simulated mutation was never released")
        original_fault()

    def observed_restore(self):
        restore_started.set()
        return original_restore(self)

    monkeypatch.setattr(coordinator.device, "simulate_fault", paused_fault)
    monkeypatch.setattr(SimulatedDevice, "restore", observed_restore)

    def run(action):
        try:
            action()
        except Exception as exc:
            failures.append(str(exc))

    mutator = threading.Thread(target=run, args=(coordinator.simulate_mutation,))
    restorer = threading.Thread(
        target=run,
        args=(OfflineCoordinator(journal_dir, device_dir, SOURCE_SHA).recover,),
    )
    mutator.start()
    try:
        assert mutation_reached.wait(timeout=5), "fault injection never reached its side effect"
        restorer.start()
        assert not restore_started.wait(timeout=0.8), "restoration raced with active mutation"
    finally:
        release_mutation.set()
        mutator.join(timeout=8)
        if restorer.ident is not None:
            restorer.join(timeout=8)
    assert not mutator.is_alive()
    assert not restorer.is_alive()
    assert failures == []
    assert restore_started.is_set()
    assert target.read_current() == BASELINE
    assert JournalStore(journal_dir).read()["state"] == "FINAL_ATTESTATION_PENDING"


@pytest.mark.parametrize("tamper", ("symlink", "hardlink", "world_readable"))
def test_offline_owner_lock_tamper_blocks_fault(tmp_path, tamper):
    coordinator, target, journal_dir, device_dir = prepared(tmp_path)
    lock = device_dir / ".offline-owner.lock"
    assert lock.is_file()
    outside = tmp_path / "protected-outside"
    outside.write_bytes(b"untouched")
    if tamper == "symlink":
        lock.unlink()
        lock.symlink_to(outside)
    elif tamper == "hardlink":
        os.link(lock, tmp_path / "extra-lock-link")
    else:
        lock.chmod(0o644)

    with pytest.raises(SimulationError, match="owner lock"):
        coordinator.simulate_mutation()

    assert JournalStore(journal_dir).read()["state"] == "PREPARED"
    assert target.read_current() == BASELINE
    assert outside.read_bytes() == b"untouched"
