"""Crash-recoverable hardware mutation transaction contracts."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Dict, List, Optional, Set

import pytest

from hw_gate.transaction import (
    JOURNAL_ROOT,
    StrictHardwareTransaction,
    TransactionError,
    TransactionRestorationError,
    TransactionState,
    recover_pending_transaction,
)
from setup import EDGECONF_PATH


SNAPSHOT = base64.b64encode(b'{"original":1}').decode("ascii")
ORIGINAL_SHA = hashlib.sha256(b'{"original":1}').hexdigest()


class Termination(BaseException):
    """Stand-in for the runner's future SIGTERM-style unwinding exception."""


class FakeSSH:
    def __init__(self, events: List[str]) -> None:
        self.events = events
        self.commands: List[str] = []
        self.scan_entries: List[str] = []
        self.validation_manifest: Optional[Dict[str, object]] = None
        self.fail_persist = False
        self.fail_recovery_restore = False
        self.fail_delete = False
        self.fail_states: Set[str] = set()
        self.config_sha = ORIGINAL_SHA

    def run(self, command: str, **_kwargs: object) -> Optional[str]:
        self.commands.append(command)
        if "PIM_JOURNAL_SCAN_OK" in command:
            self.events.append("scan")
            body = "\n".join(self.scan_entries)
            return "{0}\nPIM_JOURNAL_SCAN_OK".format(body) if body else "PIM_JOURNAL_SCAN_OK"
        if "PIM_JOURNAL_VALIDATE_OK:" in command:
            self.events.append("validate_journal")
            if self.validation_manifest is None:
                return "PIM_JOURNAL_VALIDATE_FAIL"
            payload = base64.b64encode(
                json.dumps(self.validation_manifest, separators=(",", ":")).encode("utf-8")
            ).decode("ascii")
            return "PIM_JOURNAL_VALIDATE_OK:{0}".format(payload)
        if "PIM_JOURNAL_PERSIST_OK" in command:
            self.events.append("journal")
            return None if self.fail_persist else "PIM_JOURNAL_PERSIST_OK"
        if "PIM_JOURNAL_STATE_OK" in command:
            match = re.search(r"--arg state '?([A-Z]+)'?", command)
            assert match is not None
            state = match.group(1)
            self.events.append("state:{0}".format(state))
            if state in self.fail_states:
                return None
            return "PIM_JOURNAL_STATE_OK"
        if "PIM_JOURNAL_RESTORE_OK" in command:
            self.events.append("recover_restore")
            return None if self.fail_recovery_restore else "PIM_JOURNAL_RESTORE_OK"
        if "PIM_CONFIG_HASH_OK" in command:
            self.events.append("hash")
            return "{0}\nPIM_CONFIG_HASH_OK".format(self.config_sha)
        if "PIM_JOURNAL_DELETE_OK" in command:
            self.events.append("delete")
            return None if self.fail_delete else "PIM_JOURNAL_DELETE_OK"
        raise AssertionError("unexpected remote command: {0}".format(command))


class FakeSetupManager:
    def __init__(self) -> None:
        self.events: List[str] = []
        self.ssh = FakeSSH(self.events)
        self.snapshot_ok = True
        self.snapshot_payload: Optional[str] = SNAPSHOT
        self.backup_ok = True
        self.restore_ok = True
        self.snapshot_error: Optional[BaseException] = None
        self.apply_error: Optional[BaseException] = None
        self.restore_error: Optional[BaseException] = None
        self.reboot_errors: Dict[int, BaseException] = {}
        self.reboot_count = 0

    def snapshot_config(self, conf_path: str) -> bool:
        assert conf_path == EDGECONF_PATH
        self.events.append("snapshot")
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.snapshot_ok

    def get_snapshot_payload(self, conf_path: str) -> Optional[str]:
        assert conf_path == EDGECONF_PATH
        return self.snapshot_payload

    def backup(self, conf_path: str) -> bool:
        assert conf_path == EDGECONF_PATH
        self.events.append("backup")
        return self.backup_ok

    def apply_changes(self, changes: dict, conf_path: str) -> None:
        assert changes
        assert conf_path == EDGECONF_PATH
        self.events.append("apply")
        if self.apply_error is not None:
            raise self.apply_error

    def reboot_and_wait(self, stabilize_sec: int = 30) -> None:
        del stabilize_sec
        self.reboot_count += 1
        self.events.append("reboot:{0}".format(self.reboot_count))
        error = self.reboot_errors.get(self.reboot_count)
        if error is not None:
            raise error

    def restore_from_snapshot(self, conf_path: str) -> bool:
        assert conf_path == EDGECONF_PATH
        self.events.append("restore")
        if self.restore_error is not None:
            raise self.restore_error
        return self.restore_ok


def _transaction(manager: FakeSetupManager, run_id: str = "run-1") -> StrictHardwareTransaction:
    return StrictHardwareTransaction(
        manager,
        run_id,
        {".VHL_CAM.i2c2.ch0.bps": [2000000, 2000000]},
        stabilize_sec=0,
    )


def _manifest(run_id: str = "dirty-1", state: str = "APPLIED") -> Dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "config_path": EDGECONF_PATH,
        "original_sha256": ORIGINAL_SHA,
        "created_at": "2026-08-26T01:02:03Z",
        "state": state,
    }


@pytest.mark.parametrize(
    "failure", ["snapshot", "payload", "persistent_copy", "persistent_hash", "backup"],
)
def test_each_pre_mutation_gate_aborts_before_apply(failure: str) -> None:
    """Removing any safety gate must make target mutation unreachable."""
    manager = FakeSetupManager()
    if failure == "snapshot":
        manager.snapshot_ok = False
    elif failure == "payload":
        manager.snapshot_payload = None
    elif failure in {"persistent_copy", "persistent_hash"}:
        manager.ssh.fail_persist = True
    else:
        manager.backup_ok = False

    with pytest.raises(TransactionError) as caught:
        with _transaction(manager):
            pytest.fail("measurement body must not run")

    assert caught.value.verdict == "ERROR"
    assert "apply" not in manager.events


def test_preflight_order_is_recovery_snapshot_journal_backup_apply() -> None:
    """The approved safety ruling is an execution order, not documentation."""
    manager = FakeSetupManager()

    with _transaction(manager):
        manager.events.append("measure")

    significant = [
        event for event in manager.events
        if event in {"scan", "snapshot", "journal", "backup", "apply", "measure"}
    ]
    assert significant == ["scan", "snapshot", "journal", "backup", "apply", "measure"]


def test_normal_transaction_persists_the_complete_state_machine() -> None:
    """Omitting a durable lifecycle state would make crash diagnosis ambiguous."""
    manager = FakeSetupManager()

    with _transaction(manager) as transaction:
        assert transaction.state is TransactionState.REBOOTED

    states = [event for event in manager.events if event.startswith("state:")]
    assert states == [
        "state:APPLIED",
        "state:REBOOTED",
        "state:RESTORED",
        "state:VERIFIED",
        "state:CLOSED",
    ]
    assert transaction.state is TransactionState.CLOSED


def test_pre_mutation_programmer_error_is_not_reclassified() -> None:
    """Only transaction failures, not unrelated defects, are translated to ERROR."""
    manager = FakeSetupManager()
    manager.snapshot_error = AssertionError("programmer bug")

    with pytest.raises(AssertionError, match="programmer bug"):
        with _transaction(manager):
            pytest.fail("measurement body must not run")

    assert "apply" not in manager.events


def test_journal_is_atomic_validated_mode_restricted_and_secret_free() -> None:
    """Persistent recovery data is durable without embedding config changes or secrets."""
    manager = FakeSetupManager()

    with _transaction(manager, " unsafe/id '42 ") as transaction:
        persist = next(
            command for command in manager.ssh.commands if "PIM_JOURNAL_PERSIST_OK" in command
        )
        assert transaction.run_id == "unsafe-id-42"
        assert set(transaction.manifest) == {
            "schema_version", "run_id", "config_path", "original_sha256", "created_at", "state",
        }
        assert transaction.manifest["original_sha256"] == ORIGINAL_SHA
        assert JOURNAL_ROOT in persist
        assert "chmod 700" in persist
        assert persist.count("chmod 600") >= 2
        assert "jq -e" in persist
        assert "sha256sum" in persist
        assert "sync" in persist
        assert "mv" in persist
        assert "2000000" not in persist


@pytest.mark.parametrize(
    "stage,error",
    [
        ("apply", RuntimeError("apply failed")),
        ("reboot", RuntimeError("reboot failed")),
    ],
)
def test_apply_or_initial_reboot_failure_attempts_verified_restoration(
    stage: str, error: BaseException,
) -> None:
    """An exception after apply begins cannot escape the one restoration path."""
    manager = FakeSetupManager()
    if stage == "apply":
        manager.apply_error = error
    else:
        manager.reboot_errors[1] = error

    with pytest.raises(RuntimeError, match=str(error)):
        with _transaction(manager):
            pytest.fail("measurement body must not run")

    assert "restore" in manager.events
    assert "hash" in manager.events
    assert "delete" in manager.events


@pytest.mark.parametrize(
    "body_error",
    [
        RuntimeError("measurement failed"),
        ValueError("parse failed"),
        AssertionError("assertion failed"),
    ],
)
def test_body_parse_and_assertion_failures_restore_then_preserve_original_error(
    body_error: Exception,
) -> None:
    """Business failures keep their type only after restoration is verified."""
    manager = FakeSetupManager()

    with pytest.raises(type(body_error), match=str(body_error)):
        with _transaction(manager):
            raise body_error

    assert manager.events.count("restore") == 1
    assert manager.events.count("delete") == 1


def test_sigterm_style_baseexception_uses_the_same_restoration_path() -> None:
    """Signal unwinding is not limited to Exception subclasses."""
    manager = FakeSetupManager()

    with pytest.raises(Termination, match="terminated"):
        with _transaction(manager):
            raise Termination("terminated")

    assert manager.events.count("restore") == 1
    assert manager.events.count("hash") == 1
    assert manager.events.count("delete") == 1


def test_explicit_signal_unwind_and_context_exit_are_idempotent() -> None:
    """A signal handler and __exit__ cannot race into two restore/reboot cycles."""
    manager = FakeSetupManager()

    with _transaction(manager) as transaction:
        transaction.unwind_for_signal()

    assert manager.events.count("restore") == 1
    assert manager.reboot_count == 2
    assert manager.events.count("delete") == 1
    assert transaction.state is TransactionState.CLOSED


@pytest.mark.parametrize("failure", ["restore", "restore_exception", "reboot", "hash", "delete"])
def test_cleanup_failure_overrides_a_successful_body_and_preserves_journal(failure: str) -> None:
    """No measurement PASS survives uncertain restoration or journal closure."""
    manager = FakeSetupManager()
    if failure == "restore":
        manager.restore_ok = False
    elif failure == "restore_exception":
        manager.restore_error = RuntimeError("restore transport failed")
    elif failure == "reboot":
        manager.reboot_errors[2] = RuntimeError("post-restore reboot failed")
    elif failure == "hash":
        manager.ssh.config_sha = "f" * 64
    else:
        manager.ssh.fail_delete = True

    with pytest.raises(TransactionRestorationError) as caught:
        with _transaction(manager):
            manager.events.append("pass")

    assert caught.value.verdict == "ERROR"
    if failure != "delete":
        assert "delete" not in manager.events


def test_cleanup_failure_overrides_and_chains_a_body_failure() -> None:
    """Restoration ERROR wins while retaining the original assertion diagnostic."""
    manager = FakeSetupManager()
    manager.restore_ok = False

    with pytest.raises(TransactionRestorationError) as caught:
        with _transaction(manager):
            raise AssertionError("measurement mismatch")

    assert isinstance(caught.value.__cause__, AssertionError)
    assert "measurement mismatch" in str(caught.value.__cause__)


def test_delete_failure_can_be_retried_without_a_second_restore() -> None:
    """CLOSED means verified; it does not falsely mean the journal was deleted."""
    manager = FakeSetupManager()
    manager.ssh.fail_delete = True
    transaction = _transaction(manager)

    with pytest.raises(TransactionRestorationError):
        with transaction:
            pass

    manager.ssh.fail_delete = False
    transaction.restore_and_verify()

    assert manager.events.count("restore") == 1
    assert manager.events.count("hash") == 1
    assert manager.events.count("delete") == 2


def test_repeated_apply_reboot_cycles_share_one_snapshot_and_final_restore() -> None:
    """A mixed-scenario campaign may mutate repeatedly inside one strict context."""
    manager = FakeSetupManager()
    transaction = StrictHardwareTransaction(manager, "campaign-1", stabilize_sec=0)

    with transaction:
        transaction.apply_and_reboot({".scenario": "A"})
        transaction.apply_and_reboot({".scenario": "B"})

    assert manager.events.count("snapshot") == 1
    assert manager.events.count("backup") == 1
    assert manager.events.count("apply") == 2
    assert manager.events.count("restore") == 1
    assert manager.reboot_count == 3


def test_no_pending_journal_is_a_noop() -> None:
    manager = FakeSetupManager()

    assert recover_pending_transaction(manager, stabilize_sec=0) is False
    assert manager.reboot_count == 0
    assert manager.events == ["scan"]


def test_one_valid_dirty_journal_restores_reboots_verifies_and_then_deletes() -> None:
    """Crash recovery uses the persistent original, never manager host memory."""
    manager = FakeSetupManager()
    manager.ssh.scan_entries = ["dirty-1"]
    manager.ssh.validation_manifest = _manifest()
    manager.snapshot_payload = None

    assert recover_pending_transaction(manager, stabilize_sec=0) is True

    assert "recover_restore" in manager.events
    assert "restore" not in manager.events
    assert manager.reboot_count == 1
    assert manager.events.index("hash") < manager.events.index("delete")
    validate = next(
        command for command in manager.ssh.commands if "PIM_JOURNAL_VALIDATE_OK:" in command
    )
    assert "stat -c" in validate
    assert "jq -e" in validate
    assert "sha256sum" in validate


def test_multiple_dirty_journals_fail_closed_before_restore() -> None:
    manager = FakeSetupManager()
    manager.ssh.scan_entries = ["dirty-1", "dirty-2"]

    with pytest.raises(TransactionError, match="multiple") as caught:
        recover_pending_transaction(manager, stabilize_sec=0)

    assert caught.value.verdict == "ERROR"
    assert "recover_restore" not in manager.events
    assert "delete" not in manager.events


@pytest.mark.parametrize(
    "entry,manifest",
    [
        ("bad id", _manifest("bad id")),
        ("dirty-1", None),
        ("dirty-1", {**_manifest(), "secret": "must-not-be-accepted"}),
        ("dirty-1", {**_manifest(), "original_sha256": "bad"}),
        ("dirty-1", {**_manifest(), "created_at": "2026-08-26Z"}),
        ("dirty-1", {**_manifest(), "config_path": "/tmp/other.json"}),
        ("dirty-1", {**_manifest(), "state": "NEW"}),
    ],
)
def test_malformed_dirty_journal_blocks_recovery_and_measurement(
    entry: str, manifest: Optional[Dict[str, object]],
) -> None:
    manager = FakeSetupManager()
    manager.ssh.scan_entries = [entry]
    manager.ssh.validation_manifest = manifest

    with pytest.raises(TransactionError) as caught:
        recover_pending_transaction(manager, stabilize_sec=0)

    assert caught.value.verdict == "ERROR"
    assert "recover_restore" not in manager.events
    assert "delete" not in manager.events


@pytest.mark.parametrize("failure", ["restore", "reboot", "hash"])
def test_dirty_recovery_failure_leaves_the_journal_intact(failure: str) -> None:
    manager = FakeSetupManager()
    manager.ssh.scan_entries = ["dirty-1"]
    manager.ssh.validation_manifest = _manifest()
    if failure == "restore":
        manager.ssh.fail_recovery_restore = True
    elif failure == "reboot":
        manager.reboot_errors[1] = RuntimeError("recovery reboot failed")
    else:
        manager.ssh.config_sha = "0" * 64

    with pytest.raises(TransactionRestorationError):
        recover_pending_transaction(manager, stabilize_sec=0)

    assert "delete" not in manager.events


def test_unsafe_config_path_is_rejected_before_remote_access() -> None:
    manager = FakeSetupManager()

    with pytest.raises(TransactionError, match="config path"):
        StrictHardwareTransaction(
            manager,
            "run-1",
            {".a": 1},
            conf_path="/root/shared_v/edgeconf_pim.json; reboot",
        )

    assert manager.events == []
