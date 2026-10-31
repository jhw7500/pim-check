"""Trusted owner board adapter integration tests, all offline and fake-backed."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import subprocess
from unittest.mock import Mock

import pytest

from scripts.qa_owner_recovery_controller import Baseline
from scripts.qa_owner_board_adapter import (
    AdapterBlocked, BoardSample, OfficialLeaseVerifier,
    PinnedBoardRecoveryAdapter, REQUIRED_BINARIES,
)

RUN = "owner-restoration-001"
GIT_SHA = "a" * 40
EDGE = "b" * 64
ORD = "c" * 64
RUNTIME = "d" * 64
BINS = {name: str(i + 1) * 64 for i, name in enumerate(REQUIRED_BINARIES)}
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
UNTIL = (NOW + timedelta(seconds=1800)).strftime("%Y-%m-%dT%H:%M:%SZ")
ENV = {"PIM_BOARD_LOCK_HELD": "1", "PIM_BOARD_LOCK_BOARD_ID": "pim",
       "PIM_BOARD_LOCK_OWNER_PID": "2311",
       "PIM_BOARD_LOCK_SESSION": "owner:bounded-restore",
       "PIM_BOARD_LOCK_SESSION_OWNER": "trusted-owner"}


class Runner:
    def __init__(self, rc=0):
        self.rc = rc
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, self.rc, stdout="", stderr="")


class Probe:
    def __init__(self, sample):
        self.value = sample
        self.count = 0

    def observe(self):
        self.count += 1
        return self.value


def make_lease(tmp_path, monkeypatch, *, env=None, rc=0, offset=0):
    root = tmp_path / "pinned-repo"
    root.mkdir(exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/with_pim_board.sh").write_text("#!/bin/bash\n")
    monkeypatch.setattr(
        "scripts.qa_owner_board_adapter.check_source", lambda *_: None)
    runner = Runner(rc)
    lease = OfficialLeaseVerifier(
        source_root=root, source_sha=GIT_SHA,
        expected_until=UNTIL, purpose="pim-owner-durable-restore",
        env=ENV if env is None else env,
        clock=lambda: NOW.timestamp() + offset, runner=runner)
    return lease, runner


def baseline():
    return Baseline(RUN, GIT_SHA, EDGE, ORD, RUNTIME)


def sample(**changes):
    value = BoardSample(
        target="192.168.214.4", source_sha=GIT_SHA,
        observed_at_epoch=int(NOW.timestamp()),
        journal_state="DIRTY", journal_phase="REBOOTED",
        journal_run_id=RUN, journal_original_sha=EDGE,
        edgeconf_sha="e" * 64, ord_conf_sha=ORD,
        runtime_sha="f" * 64, led_flags=(False, False, False, False),
        binary_hashes=BINS.copy(), consumers_ready=False,
        writer_closed=True, owner_idle=True,
        writer_fence_session=ENV["PIM_BOARD_LOCK_SESSION"])
    return replace(value, **changes)


def adapter(tmp_path, monkeypatch, *, current=None, lease=None, manager=None):
    lease = lease or make_lease(tmp_path, monkeypatch)[0]
    probe = Probe(current if current is not None else sample())
    if manager is None:
        manager = Mock()
        manager.ssh = object()
        manager.reboot_and_wait = Mock()
    return PinnedBoardRecoveryAdapter(
        baseline=baseline(), lease=lease, inspector=probe,
        setup_manager=manager, installed_hashes=BINS), probe


def test_real_wrapper_check_held_arguments_are_exact(tmp_path, monkeypatch):
    lease, runner = make_lease(tmp_path, monkeypatch)
    proof = lease.verify()
    assert proof.session == ENV["PIM_BOARD_LOCK_SESSION"]
    assert proof.remaining_seconds == 1800
    assert len(runner.calls) == 1
    argv, options = runner.calls[0]
    assert argv == [
        "/bin/bash", str(lease.source_root / "scripts/with_pim_board.sh"),
        "--check-held", "--until", UNTIL,
        "--long-lease", "true", "--purpose", "pim-owner-durable-restore"]
    assert options["shell"] is False
    assert options["timeout"] <= 15


@pytest.mark.parametrize("change", [
    {"PIM_BOARD_LOCK_HELD": "0"},
    {"PIM_BOARD_LOCK_BOARD_ID": "other"},
    {"PIM_BOARD_LOCK_OWNER_PID": ""},
    {"PIM_BOARD_LOCK_SESSION": ""},
    {"PIM_BOARD_LOCK_OWNER_PID": "not-pid"},
])
def test_marker_spoof_or_missing_cannot_skip_official_verifier(tmp_path, monkeypatch, change):
    env = {**ENV, **change}
    lease, runner = make_lease(tmp_path, monkeypatch, env=env)
    with pytest.raises(AdapterBlocked):
        lease.verify()
    assert runner.calls == []


@pytest.mark.parametrize("offset", [901, 1801])
def test_insufficient_lease_time_is_rejected_before_wrapper(tmp_path, monkeypatch, offset):
    lease, runner = make_lease(tmp_path, monkeypatch, offset=offset)
    with pytest.raises(AdapterBlocked):
        lease.verify()
    assert runner.calls == []


def test_actual_wrapper_rejection_blocks(tmp_path, monkeypatch):
    lease, runner = make_lease(tmp_path, monkeypatch, rc=1)
    with pytest.raises(AdapterBlocked):
        lease.verify()
    assert len(runner.calls) == 1


def test_pinned_board_witness_fields_from_lease_and_probe(tmp_path, monkeypatch):
    bound, probe = adapter(tmp_path, monkeypatch)
    result = bound.observe()
    assert result.source_sha == GIT_SHA
    assert result.exclusive_lease_attested is True
    assert result.installed_binaries_attested is True
    assert result.lease_session == ENV["PIM_BOARD_LOCK_SESSION"]
    assert result.on_board_journal_state == "DIRTY"
    assert result.on_board_run_id == RUN
    assert result.on_board_original_sha == EDGE
    assert result.all_led_off is True
    assert probe.count == 1


@pytest.mark.parametrize("wrong", [
    {"target": "192.168.214.5"},
    {"source_sha": "f" * 40},
    {"observed_at_epoch": int(NOW.timestamp()) - 32},
    {"observed_at_epoch": int(NOW.timestamp()) + 1},
    {"led_flags": (False, True, False, False)},
    {"led_flags": (False, False, False)},
    {"binary_hashes": {}},
    {"binary_hashes": {"gstApp": "a" * 64}},
    {"ord_conf_sha": "f" * 64},
    {"owner_idle": False},
    {"writer_closed": False},
    {"writer_fence_session": None},
    {"writer_fence_session": "foreign-owner"},
])
def test_untrusted_readiness_and_binary_inputs_block_without_restoring(tmp_path, monkeypatch, wrong):
    obj, probe = adapter(tmp_path, monkeypatch, current=sample(**wrong))
    with pytest.raises(AdapterBlocked):
        obj.observe()
    assert probe.count == 1


def test_only_known_dirty_board_journal_can_trigger_existing_restorer(tmp_path, monkeypatch):
    bound, probe = adapter(tmp_path, monkeypatch)
    called = []
    def restorer(manager, *, conf_path, stabilize_sec):
        called.append((manager, conf_path, stabilize_sec))
        probe.value = sample(journal_state="ABSENT", journal_phase=None,
                             journal_run_id=None, journal_original_sha=None,
                             edgeconf_sha=EDGE, runtime_sha=RUNTIME,
                             consumers_ready=True)
        return True

    monkeypatch.setattr("scripts.qa_owner_board_adapter.recover_pending_transaction", restorer)
    bound.recover_validated_board_journal(run_id=RUN, original_sha256=EDGE)
    assert len(called) == 1
    assert probe.count == 1  # exact same pre-effect journal state used
    assert called[0][1] == "/root/shared_v/edgeconf_pim.json"
    assert called[0][2] >= 30
    assert bound.observe().on_board_journal_state == "ABSENT"


@pytest.mark.parametrize("phase", ["JOURNALED", "RESTORED", "VERIFIED", "CLOSED", "FOREIGN"])
def test_uncertain_journal_phase_refuses_automatic_second_reboot(
        tmp_path, monkeypatch, phase):
    bound, _ = adapter(tmp_path, monkeypatch,
                       current=sample(journal_phase=phase))
    called = []
    monkeypatch.setattr("scripts.qa_owner_board_adapter.recover_pending_transaction",
                        lambda *a, **kw: called.append(True))
    with pytest.raises(AdapterBlocked):
        bound.recover_validated_board_journal(run_id=RUN, original_sha256=EDGE)
    assert not called


def test_mismatched_original_hash_refuses_restorer(tmp_path, monkeypatch):
    bound, _ = adapter(tmp_path, monkeypatch)
    with pytest.raises(AdapterBlocked):
        bound.recover_validated_board_journal(run_id=RUN, original_sha256="f" * 64)


def test_failed_original_restorer_never_reports_success(tmp_path, monkeypatch):
    bound, _ = adapter(tmp_path, monkeypatch)
    monkeypatch.setattr("scripts.qa_owner_board_adapter.recover_pending_transaction",
                        lambda *a, **kw: False)
    with pytest.raises(AdapterBlocked):
        bound.recover_validated_board_journal(run_id=RUN, original_sha256=EDGE)


def test_no_second_restore_within_same_adapter_after_success(tmp_path, monkeypatch):
    bound, probe = adapter(tmp_path, monkeypatch)
    monkeypatch.setattr("scripts.qa_owner_board_adapter.recover_pending_transaction",
                        lambda *a, **kw: True)
    bound.recover_validated_board_journal(run_id=RUN, original_sha256=EDGE)
    with pytest.raises(AdapterBlocked):
        bound.recover_validated_board_journal(run_id=RUN, original_sha256=EDGE)


def test_adapter_exports_no_mutation_without_owner_method():
    import scripts.qa_owner_board_adapter as module
    assert not hasattr(module, "run_campaign")
    assert not hasattr(module, "deploy")
    assert not hasattr(module, "delete_recordings")


def test_lease_session_changed_during_probe_is_rejected(tmp_path, monkeypatch):
    lease, _runner = make_lease(tmp_path, monkeypatch)
    obj, probe = adapter(tmp_path, monkeypatch, lease=lease)

    def changed():
        lease.env["PIM_BOARD_LOCK_SESSION"] = "foreign-owner"
        return sample()

    probe.observe = changed
    with pytest.raises(AdapterBlocked, match="lease changed"):
        obj.observe()


def test_lease_lost_after_probe_is_rejected(tmp_path, monkeypatch):
    lease, runner = make_lease(tmp_path, monkeypatch)
    obj, _probe = adapter(tmp_path, monkeypatch, lease=lease)
    def fail_on_second(argv, **kwargs):
        runner.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0 if len(runner.calls) == 1 else 1,
            stdout="", stderr="")

    lease.runner = fail_on_second
    with pytest.raises(AdapterBlocked, match="lease not confirmed"):
        obj.observe()
