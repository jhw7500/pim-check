"""Real SIGKILL of a fake-board recovery child, without target access.

These are OS-level subprocess/crash tests, NOT systemd integration and
NOT physical board restoration. The fake board is one ordinary tmp file.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path

from scripts.qa_durable_journal import JournalStore

SOURCE = "a" * 40
EDGE = "b" * 64
ORD = "c" * 64
RUNTIME = "d" * 64
RUN = "sigkill-owner-test-001"

CHILD = r'''
import json, os, signal, sys, time
from pathlib import Path

from scripts.qa_owner_recovery_controller import Baseline, BoardEvidence, reconcile
from scripts.qa_durable_journal import JournalStore

state = Path(sys.argv[1])
board = Path(sys.argv[2])
mode = sys.argv[3]
src, edge, ord_hash, runtime_hash = "a"*40, "b"*64, "c"*64, "d"*64

class FakeBoard:
    def observe(self):
        if mode == "hold_owner_lock":
            print("LOCK_HELD", flush=True)
            time.sleep(30)
        data = json.loads(board.read_text())
        clean = data["clean"] is True
        return BoardEvidence(
            target="192.168.214.4", source_sha=src,
            source_attested=True, installed_binaries_attested=True,
            exclusive_lease_attested=True, lease_session="fake-owner-lease-1",
            lease_remaining_seconds=1800, age_seconds=0,
            on_board_journal_state="ABSENT" if clean else "DIRTY",
            on_board_run_id=None if clean else "sigkill-owner-test-001",
            on_board_original_sha=None if clean else edge,
            edgeconf_sha=edge if clean else "f"*64,
            ord_conf_sha=ord_hash,
            runtime_sha=runtime_hash if clean else "e"*64,
            all_led_off=True, consumers_ready=clean,
            writer_closed=True, owner_idle=True,
            writer_fence_session="fake-owner-lease-1")

    def recover_validated_board_journal(self, *, run_id, original_sha256):
        data = json.loads(board.read_text())
        assert run_id == "sigkill-owner-test-001" and original_sha256 == edge
        assert not data["clean"]
        if mode == "kill_before_external_effect":
            print("BEFORE_FAKE_EFFECT", flush=True)
            os.kill(os.getpid(), signal.SIGKILL)
        data = {"clean": True, "restore_calls": data["restore_calls"] + 1}
        temporary = board.with_suffix(".pending")
        with temporary.open("w") as file:
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, board)
        parent = os.open(board.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(parent)
        finally: os.close(parent)
        print("FAKE_RESTORATION_DURABLE", flush=True)
        if mode == "kill_after_external_effect":
            os.kill(os.getpid(), signal.SIGKILL)
        if mode == "perform_restore":
            return
        raise AssertionError("second external restoration must not occur")

result = reconcile(
    JournalStore(state),
    Baseline("sigkill-owner-test-001", src, edge, ord_hash, runtime_hash),
    FakeBoard(), source_checkout_verified=True)
print(json.dumps(result), flush=True)
'''


def prepare(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    board = tmp_path / "synthetic-board.json"
    board.write_text(json.dumps({"clean": False, "restore_calls": 0}))
    store = JournalStore(state)
    rec = store.begin(run_id=RUN, source_sha=SOURCE, baseline_sha256=EDGE)
    for phase in ("BASELINE_SAVED", "MUTATION_PENDING"):
        rec = store.transition(phase, expected_revision=rec["revision"])
    return store, board


def child(state, board, mode):
    return subprocess.run(
        [sys.executable, "-B", "-c", CHILD, str(state), str(board), mode],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=15, check=False)


def test_sigkill_post_remote_effect_recovery_state_is_reconciled_once(tmp_path):
    store, board = prepare(tmp_path)
    first = child(store.directory, board, "kill_after_external_effect")
    assert first.returncode == -signal.SIGKILL
    assert "FAKE_RESTORATION_DURABLE" in first.stdout
    assert store.read()["state"] == "RESTORE_PENDING"
    assert json.loads(board.read_text()) == {"clean": True, "restore_calls": 1}

    # A NEW owner process acquires the lock after the OS killed its predecessor.
    second = child(store.directory, board, "must_not_recover_again")
    assert second.returncode == 0, second.stderr
    final = json.loads(second.stdout.strip())
    assert final["state"] == "FINAL_ATTESTATION_PENDING"
    assert final["execution_authorized"] is False
    assert final["recovery_callback_called"] is False
    assert store.read()["state"] == "FINAL_ATTESTATION_PENDING"
    assert json.loads(board.read_text())["restore_calls"] == 1

    # A third service start cannot relaunch the 4ch test or remote restore.
    third = child(store.directory, board, "must_not_recover_again")
    assert third.returncode == 0, third.stderr
    final2 = json.loads(third.stdout.strip())
    assert final2["reason"] == "FINAL_ATTESTATION_REQUIRED"
    assert final2["recovery_callback_called"] is False
    assert json.loads(board.read_text())["restore_calls"] == 1



def test_sigkill_before_external_effect_restarts_restore_exactly_once(tmp_path):
    store, board = prepare(tmp_path)
    first = child(store.directory, board, "kill_before_external_effect")
    assert first.returncode == -signal.SIGKILL
    assert "BEFORE_FAKE_EFFECT" in first.stdout
    assert store.read()["state"] == "RESTORE_PENDING"
    assert json.loads(board.read_text()) == {"clean": False, "restore_calls": 0}
    second = child(store.directory, board, "perform_restore")
    assert second.returncode == 0, second.stderr
    result = json.loads(second.stdout.strip().splitlines()[-1])
    assert result["state"] == "FINAL_ATTESTATION_PENDING"
    assert result["recovery_callback_called"] is True
    assert json.loads(board.read_text()) == {"clean": True, "restore_calls": 1}
    third = child(store.directory, board, "must_not_recover_again")
    assert third.returncode == 0, third.stderr
    assert json.loads(board.read_text())["restore_calls"] == 1


def test_real_concurrent_owner_process_cannot_restore(tmp_path):
    import select
    store, board = prepare(tmp_path)
    blocking = subprocess.Popen(
        [sys.executable, "-B", "-c", CHILD, str(store.directory),
         str(board), "hold_owner_lock"],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        assert blocking.stdout is not None
        ready, _, _ = select.select([blocking.stdout], [], [], 10)
        assert ready, "fake owner did not acquire lock"
        assert blocking.stdout.readline().strip() == "LOCK_HELD"
        assert store.read()["state"] == "RESTORE_PENDING"
        competing = child(store.directory, board, "perform_restore")
        assert competing.returncode != 0
        assert "exclusive owner lock unavailable" in competing.stderr
        assert json.loads(board.read_text())["restore_calls"] == 0
    finally:
        if blocking.poll() is None:
            blocking.kill()
        blocking.communicate(timeout=10)
    restarted = child(store.directory, board, "perform_restore")
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(board.read_text())["restore_calls"] == 1
    assert store.read()["state"] == "FINAL_ATTESTATION_PENDING"
