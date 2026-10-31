"""Offline owner-restoration controller tests: ALL hardware uses fake adapter."""
from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.qa_durable_journal import JournalStore
from scripts.qa_owner_recovery_controller import (
    Baseline, BoardEvidence, RecoveryBlocked, reconcile as _reconcile,
)


def reconcile(*args, **kwargs):
    # The mock suite explicitly vouches for the *synthetic* source pin;
    # production defaults to DENY when this proof is omitted.
    kwargs.setdefault("source_checkout_verified", True)
    return _reconcile(*args, **kwargs)

RUN = "bounded-4ch-restore-001"
SOURCE = "a" * 40
EDGE = "b" * 64
ORD = "c" * 64
RUNTIME = "d" * 64


def baseline():
    return Baseline(RUN, SOURCE, EDGE, ORD, RUNTIME)


def evidence(**changes):
    data = BoardEvidence(
        target="192.168.214.4", source_sha=SOURCE,
        source_attested=True, installed_binaries_attested=True,
        exclusive_lease_attested=True, lease_session="owner-lease-001",
        lease_remaining_seconds=1800, age_seconds=2,
        on_board_journal_state="DIRTY", on_board_run_id=RUN,
        on_board_original_sha=EDGE, edgeconf_sha="e" * 64,
        ord_conf_sha=ORD, runtime_sha="f" * 64,
        all_led_off=True, consumers_ready=False, writer_closed=True,
        owner_idle=True)
    return replace(data, **changes)


def clean(**changes):
    restored = evidence(on_board_journal_state="ABSENT", on_board_run_id=None,
                        on_board_original_sha=None, edgeconf_sha=EDGE,
                        runtime_sha=RUNTIME, consumers_ready=True)
    return replace(restored, **changes)


def journal(tmp_path, state):
    path = tmp_path / "owner"
    path.mkdir(mode=0o700)
    store = JournalStore(path)
    row = store.begin(run_id=RUN, source_sha=SOURCE, baseline_sha256=EDGE)
    for next_state in ("BASELINE_SAVED", "MUTATION_PENDING", "RESTORE_PENDING",
                       "FINAL_ATTESTATION_PENDING"):
        if row["state"] == state:
            break
        row = store.transition(next_state, expected_revision=row["revision"])
    assert row["state"] == state
    return store


class FakeAdapter:
    def __init__(self, before, after=None, *, interrupted=False):
        self.current = before
        self.after = after or clean()
        self.calls = []
        self.interrupted = interrupted

    def observe(self):
        self.calls.append("observe")
        return self.current

    def recover_validated_board_journal(self, *, run_id, original_sha256):
        self.calls.append(("restore", run_id, original_sha256))
        self.current = self.after
        if self.interrupted:
            self.interrupted = False
            raise InterruptedError("test: client died AFTER target restored")


@pytest.mark.parametrize("state", ["PREPARED", "BASELINE_SAVED"])
def test_non_mutated_journals_never_contact_the_board(tmp_path, state):
    adapter = FakeAdapter(evidence())
    result = reconcile(journal(tmp_path, state), baseline(), adapter)
    assert result["reason"] == "NO_MUTATION_CONFIRMED"
    assert not adapter.calls
    assert result["execution_authorized"] is False


def test_recovery_persists_intent_before_external_adapter_call(tmp_path):
    store = journal(tmp_path, "MUTATION_PENDING")
    adapter = FakeAdapter(evidence(), clean())
    result = reconcile(store, baseline(), adapter)
    assert adapter.calls == ["observe", ("restore", RUN, EDGE), "observe"]
    assert result["state"] == "FINAL_ATTESTATION_PENDING"
    assert store.read()["state"] == "FINAL_ATTESTATION_PENDING"
    assert result["independent_final_attestation_required"] is True
    assert result["execution_authorized"] is False


def test_restart_after_restore_but_before_ack_does_not_restore_twice(tmp_path):
    store = journal(tmp_path, "RESTORE_PENDING")
    adapter = FakeAdapter(evidence(), clean(), interrupted=True)
    with pytest.raises(InterruptedError):
        reconcile(store, baseline(), adapter)
    assert store.read()["state"] == "RESTORE_PENDING"
    original_calls = len([c for c in adapter.calls if isinstance(c, tuple)])
    result = reconcile(store, baseline(), adapter)
    assert len([c for c in adapter.calls if isinstance(c, tuple)]) == original_calls == 1
    assert result["state"] == "FINAL_ATTESTATION_PENDING"


def test_existing_final_attestation_is_never_automatically_closed(tmp_path):
    store = journal(tmp_path, "FINAL_ATTESTATION_PENDING")
    adapter = FakeAdapter(evidence())
    result = reconcile(store, baseline(), adapter)
    assert result["reason"] == "FINAL_ATTESTATION_REQUIRED"
    assert not adapter.calls
    assert store.read()["state"] == "FINAL_ATTESTATION_PENDING"


@pytest.mark.parametrize("wrong", [
    {"exclusive_lease_attested": False},
    {"lease_remaining_seconds": 899},
    {"lease_remaining_seconds": True},
    {"age_seconds": 31},
    {"age_seconds": -1},
    {"target": "192.168.214.5"},
    {"source_sha": "e" * 40},
    {"source_attested": False},
    {"installed_binaries_attested": False},
    {"owner_idle": False},
    {"writer_closed": False},
    {"all_led_off": False},
    {"ord_conf_sha": "f" * 64},
    {"on_board_original_sha": "f" * 64},
    {"on_board_run_id": "foreign-run"},
    {"on_board_journal_state": "UNVERIFIED"},
])
def test_unsafe_before_proof_blocks_without_hardware_restore(tmp_path, wrong):
    store = journal(tmp_path, "RESTORE_PENDING")
    adapter = FakeAdapter(evidence(**wrong))
    with pytest.raises(RecoveryBlocked):
        reconcile(store, baseline(), adapter)
    assert not any(isinstance(x, tuple) for x in adapter.calls)
    assert store.read()["state"] == "RESTORE_PENDING"


def test_orphaned_mismatch_without_journal_never_blindly_restores(tmp_path):
    store = journal(tmp_path, "RESTORE_PENDING")
    adapter = FakeAdapter(evidence(on_board_journal_state="ABSENT",
                                   on_board_run_id=None, on_board_original_sha=None))
    with pytest.raises(RecoveryBlocked, match="baseline"):
        reconcile(store, baseline(), adapter)
    assert adapter.calls == ["observe"]


@pytest.mark.parametrize("wrong", [
    {"runtime_sha": "f" * 64}, {"consumers_ready": False},
    {"writer_closed": False}, {"all_led_off": False},
])
def test_correct_source_without_runtime_health_does_not_ack(tmp_path, wrong):
    store = journal(tmp_path, "RESTORE_PENDING")
    bad = clean(**wrong)
    adapter = FakeAdapter(bad)
    with pytest.raises(RecoveryBlocked):
        reconcile(store, baseline(), adapter)
    assert store.read()["state"] == "RESTORE_PENDING"
    assert adapter.calls == ["observe"]


def test_post_restore_lease_identity_change_blocks_final_ack(tmp_path):
    store = journal(tmp_path, "RESTORE_PENDING")
    adapter = FakeAdapter(evidence(), clean(lease_session="different-owner-lease"))
    with pytest.raises(RecoveryBlocked):
        reconcile(store, baseline(), adapter)
    assert store.read()["state"] == "RESTORE_PENDING"
    assert len([x for x in adapter.calls if isinstance(x, tuple)]) == 1


def test_post_restore_stale_or_unready_leaves_recovery_pending(tmp_path):
    store = journal(tmp_path, "RESTORE_PENDING")
    adapter = FakeAdapter(evidence(), clean(age_seconds=31))
    with pytest.raises(RecoveryBlocked):
        reconcile(store, baseline(), adapter)
    assert store.read()["state"] == "RESTORE_PENDING"


def test_untrusted_host_source_or_journal_does_not_contact_adapter(tmp_path):
    store = journal(tmp_path, "MUTATION_PENDING")
    adapter = FakeAdapter(evidence())
    with pytest.raises(RecoveryBlocked):
        reconcile(store, baseline(), adapter, source_checkout_verified=False)
    assert adapter.calls == []
    with pytest.raises(Exception):
        reconcile(store, replace(baseline(), source_sha="b" * 40), adapter)
    assert adapter.calls == []


def test_missing_host_journal_is_not_a_clean_run(tmp_path):
    store = JournalStore(tmp_path)
    adapter = FakeAdapter(evidence())
    with pytest.raises(RecoveryBlocked):
        reconcile(store, baseline(), adapter)
    assert adapter.calls == []


def test_second_owner_cannot_compete_for_restore(tmp_path):
    from scripts.qa_owner_supervisor import owner_lock

    store = journal(tmp_path, "MUTATION_PENDING")
    adapter = FakeAdapter(evidence())
    with owner_lock(store.directory):
        with pytest.raises(RecoveryBlocked, match="owner lock"):
            reconcile(store, baseline(), adapter)
    assert adapter.calls == []
    assert store.read()["state"] == "MUTATION_PENDING"


def test_unsafe_owner_lock_releases_file_descriptors(tmp_path):
    from scripts.qa_owner_supervisor import OwnerServiceError, owner_lock
    import os

    store = journal(tmp_path, "RESTORE_PENDING")
    lock = store.directory / ".owner-service.lock"
    other = tmp_path / "outside"
    other.write_bytes(b"not to be overwritten")
    lock.symlink_to(other)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(OwnerServiceError):
        with owner_lock(store.directory):
            pass
    assert len(os.listdir("/proc/self/fd")) == before
    assert other.read_bytes() == b"not to be overwritten"


def test_no_source_attestation_argument_defaults_to_block(tmp_path):
    store = journal(tmp_path, "RESTORE_PENDING")
    adapter = FakeAdapter(evidence())
    with pytest.raises(RecoveryBlocked, match="deployed source"):
        _reconcile(store, baseline(), adapter)
    assert adapter.calls == []
    assert store.read()["state"] == "RESTORE_PENDING"
