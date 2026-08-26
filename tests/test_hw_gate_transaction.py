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
        self.scan_command_failure = False
        self.scan_raw_output: Optional[str] = None
        self.scan_raw_output_set = False
        self.root_kind = "directory"
        self.root_owner = "0:0"
        self.root_mode = "700"
        self.persist_root_kind = "directory"
        self.persist_root_owner = "0:0"
        self.persist_root_mode = "700"
        self.validation_manifest: Optional[Dict[str, object]] = None
        self.persist_failure: Optional[str] = None
        self.validation_failure: Optional[str] = None
        self.fail_recovery_restore = False
        self.fail_delete = False
        self.fail_states: Set[str] = set()
        self.config_sha = ORIGINAL_SHA

    @staticmethod
    def _ordered(command: str, tokens: List[str]) -> bool:
        cursor = 0
        for token in tokens:
            position = command.find(token, cursor)
            if position < 0:
                return False
            cursor = position + len(token)
        return True

    def _scan_success(self, command: str) -> str:
        if "PIM_JOURNAL_SCAN_OK:" not in command:
            body = "\n".join(self.scan_entries)
            return "{0}\nPIM_JOURNAL_SCAN_OK".format(body) if body else "PIM_JOURNAL_SCAN_OK"
        raw = b"".join(
            ("{0}/{1}".format(JOURNAL_ROOT, entry)).encode("utf-8") + b"\0"
            for entry in self.scan_entries
        )
        return "PIM_JOURNAL_SCAN_OK:{0}".format(base64.b64encode(raw).decode("ascii"))

    def _root_check_blocks(self, command: str, *, persistence: bool) -> bool:
        kind = self.persist_root_kind if persistence else self.root_kind
        owner = self.persist_root_owner if persistence else self.root_owner
        mode = self.persist_root_mode if persistence else self.root_mode
        symlink_check = "[ ! -L {0} ]".format(JOURNAL_ROOT)
        directory_check = "[ -d {0} ]".format(JOURNAL_ROOT)
        ownership_check = "stat -c '%u:%g:%a' {0})\" = 0:0:700".format(JOURNAL_ROOT)
        if kind == "symlink" and command.count(symlink_check) >= 2:
            return True
        if kind != "directory" and directory_check in command:
            return True
        if owner != "0:0" and ownership_check in command:
            return True
        if mode != "700" and ownership_check in command:
            return True
        return False

    def run(self, command: str, **_kwargs: object) -> Optional[str]:
        self.commands.append(command)
        if "PIM_JOURNAL_SCAN_OK" in command:
            self.events.append("scan")
            if self.scan_raw_output_set:
                return self.scan_raw_output
            if self.scan_command_failure:
                if command.startswith("set -eu;") and "find " in command and "base64 -w0" in command:
                    return None
                return self._scan_success(command)
            if self._root_check_blocks(command, persistence=False):
                return None
            return self._scan_success(command)
        if "PIM_JOURNAL_VALIDATE_OK:" in command:
            self.events.append("validate_journal")
            if self.validation_manifest is None:
                return "PIM_JOURNAL_VALIDATE_FAIL"
            dirty_dir = "{0}/dirty-1".format(JOURNAL_ROOT)
            original = "{0}/edgeconf_pim.json.original".format(dirty_dir)
            failure_tokens = {
                "root_symlink": "[ ! -L {0} ]".format(JOURNAL_ROOT),
                "root_owner": "stat -c '%u:%g:%a' {0})\" = 0:0:700".format(JOURNAL_ROOT),
                "root_mode": "stat -c '%u:%g:%a' {0})\" = 0:0:700".format(JOURNAL_ROOT),
                "run_symlink": "[ ! -L {0} ]".format(dirty_dir),
                "run_owner": "stat -c '%u:%g:%a' {0})\" = 0:0:700".format(dirty_dir),
                "run_mode": "stat -c '%u:%g:%a' {0})\" = 0:0:700".format(dirty_dir),
                "file_symlink": "[ ! -L {0} ]".format(original),
                "file_owner": "stat -c '%u:%g:%a' {0})\" = 0:0:600".format(original),
                "file_mode": "stat -c '%u:%g:%a' {0})\" = 0:0:600".format(original),
                "count": "find {0} -mindepth 1 -maxdepth 1 -printf x".format(dirty_dir),
                "jq": "jq -e 'type == \"object\"",
                "hash": "sha256sum {0}".format(original),
                "command": "set -eu;",
            }
            required = failure_tokens.get(self.validation_failure or "")
            if required is not None and required in command:
                return None
            payload = base64.b64encode(
                json.dumps(self.validation_manifest, separators=(",", ":")).encode("utf-8")
            ).decode("ascii")
            return "PIM_JOURNAL_VALIDATE_OK:{0}".format(payload)
        if "PIM_JOURNAL_PERSIST_OK" in command:
            self.events.append("journal")
            if self._root_check_blocks(command, persistence=True):
                return None
            run_dir = "{0}/run-1".format(JOURNAL_ROOT)
            original_tmp = "{0}/edgeconf_pim.json.original.tmp".format(run_dir)
            original = "{0}/edgeconf_pim.json.original".format(run_dir)
            failure_tokens = {
                "copy": "base64 -d > {0}".format(original_tmp),
                "hash": "sha256sum {0}".format(original_tmp),
                "jq": "jq -e . {0}".format(original_tmp),
                "run_mode": "stat -c '%u:%g:%a' {0})\" = 0:0:700".format(run_dir),
                "run_owner": "stat -c '%u:%g:%a' {0})\" = 0:0:700".format(run_dir),
                "file_mode": "stat -c '%u:%g:%a' {0})\" = 0:0:600".format(original),
                "file_owner": "stat -c '%u:%g:%a' {0})\" = 0:0:600".format(original),
                "command": "set -eu;",
            }
            required = failure_tokens.get(self.persist_failure or "")
            if required is not None and required in command:
                return None
            return "PIM_JOURNAL_PERSIST_OK"
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
    elif failure == "persistent_copy":
        manager.ssh.persist_failure = "copy"
    elif failure == "persistent_hash":
        manager.ssh.persist_failure = "hash"
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


def test_absent_root_is_created_then_verified_before_enumeration() -> None:
    """Creation is safe only when ownership/type/mode are verified before find."""
    manager = FakeSetupManager()

    assert recover_pending_transaction(manager, stabilize_sec=0) is False

    scan = manager.ssh.commands[0]
    assert FakeSSH._ordered(scan, [
        "[ ! -e", "[ ! -L", "mkdir", "chown 0:0", "chmod 700",
        "[ ! -L", "[ -d", "stat -c '%u:%g:%a'", "0:0:700", "find ", "base64 -w0",
    ])


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("root_kind", "symlink"),
        ("root_kind", "file"),
        ("root_owner", "1000:1000"),
        ("root_mode", "755"),
    ],
)
def test_unsafe_scan_root_fails_before_snapshot(attribute: str, value: str) -> None:
    """An existing unsafe root is rejected, never followed or repaired."""
    manager = FakeSetupManager()
    setattr(manager.ssh, attribute, value)

    with pytest.raises(TransactionError):
        with _transaction(manager):
            pytest.fail("measurement body must not run")

    assert manager.events == ["scan"]


def test_find_failure_cannot_be_masked_by_a_trailing_success_marker() -> None:
    """A non-zero enumeration command must prevent any apparent scan success."""
    manager = FakeSetupManager()
    manager.ssh.scan_command_failure = True

    with pytest.raises(TransactionError):
        recover_pending_transaction(manager, stabilize_sec=0)

    assert manager.events == ["scan"]


@pytest.mark.parametrize(
    "output",
    [
        "",
        "PIM_JOURNAL_SCAN_OK",
        "PIM_JOURNAL_SCAN_OK:ZmFrZS10cnVuY2F0ZWQ",
        "partial-entry\nPIM_JOURNAL_SCAN_OK:",
    ],
)
def test_empty_or_partial_scan_output_is_an_error(output: str) -> None:
    """Only one complete base64/NUL envelope can authorize an empty or exact scan."""
    manager = FakeSetupManager()
    manager.ssh.scan_raw_output_set = True
    manager.ssh.scan_raw_output = output

    with pytest.raises(TransactionError):
        recover_pending_transaction(manager, stabilize_sec=0)

    assert manager.events == ["scan"]


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("persist_root_kind", "symlink"),
        ("persist_root_kind", "file"),
        ("persist_root_owner", "1000:1000"),
        ("persist_root_mode", "755"),
    ],
)
def test_root_toctou_before_persistence_aborts_apply(attribute: str, value: str) -> None:
    """Persistence revalidates the root instead of trusting the earlier scan."""
    manager = FakeSetupManager()
    setattr(manager.ssh, attribute, value)

    with pytest.raises(TransactionError):
        with _transaction(manager):
            pytest.fail("measurement body must not run")

    assert "snapshot" in manager.events
    assert "apply" not in manager.events


@pytest.mark.parametrize(
    "failure",
    ["copy", "hash", "jq", "run_mode", "run_owner", "file_mode", "file_owner", "command"],
)
def test_distinct_persistence_safety_failures_abort_apply(failure: str) -> None:
    """Every copy/hash/JSON/mode/owner/command gate independently blocks mutation."""
    manager = FakeSetupManager()
    manager.ssh.persist_failure = failure

    with pytest.raises(TransactionError):
        with _transaction(manager):
            pytest.fail("measurement body must not run")

    assert "journal" in manager.events
    assert "apply" not in manager.events


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
    assert transaction.state_history == (
        TransactionState.NEW,
        TransactionState.SNAPSHOTTED,
        TransactionState.JOURNALED,
        TransactionState.APPLIED,
        TransactionState.REBOOTED,
        TransactionState.RESTORED,
        TransactionState.VERIFIED,
        TransactionState.CLOSED,
    )


def test_failed_preflight_records_an_honest_error_terminal_state() -> None:
    manager = FakeSetupManager()
    manager.backup_ok = False
    transaction = _transaction(manager)

    with pytest.raises(TransactionError):
        with transaction:
            pytest.fail("measurement body must not run")

    assert transaction.terminal_state is TransactionState.ERROR
    assert transaction.state_history == (
        TransactionState.NEW,
        TransactionState.SNAPSHOTTED,
        TransactionState.JOURNALED,
        TransactionState.ERROR,
    )


def test_body_failure_closes_recovery_then_records_error_outcome() -> None:
    manager = FakeSetupManager()
    transaction = _transaction(manager)

    with pytest.raises(ValueError, match="parse failed"):
        with transaction:
            raise ValueError("parse failed")

    assert transaction.state is TransactionState.CLOSED
    assert transaction.terminal_state is TransactionState.ERROR
    assert transaction.state_history[-2:] == (
        TransactionState.CLOSED,
        TransactionState.ERROR,
    )


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
        assert FakeSSH._ordered(persist, [
            "[ ! -e", "[ ! -L", "mkdir", "chown 0:0", "chmod 700",
            "[ ! -L", "[ -d", "stat -c '%u:%g:%a'", "0:0:700",
            "mkdir", "chown 0:0", "chmod 700", "0:0:700",
            "base64 -d", "jq -e", "sha256sum", "mv",
            "base64 -d", "jq -e", "mv", "0:0:600", "sync",
        ])


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

    transaction = _transaction(manager)
    with pytest.raises(TransactionRestorationError) as caught:
        with transaction:
            manager.events.append("pass")

    assert caught.value.verdict == "ERROR"
    assert transaction.terminal_state is TransactionState.ERROR
    assert transaction.state_history[-1] is TransactionState.ERROR
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


@pytest.mark.parametrize(
    "failure",
    [
        "root_symlink", "root_owner", "root_mode", "run_symlink", "run_owner", "run_mode",
        "file_symlink", "file_owner", "file_mode", "count", "jq", "hash", "command",
    ],
)
def test_each_dirty_journal_integrity_failure_blocks_restore(failure: str) -> None:
    """Skipping any root/run/file/count/JSON/hash check makes this regression fail."""
    manager = FakeSetupManager()
    manager.ssh.scan_entries = ["dirty-1"]
    manager.ssh.validation_manifest = _manifest()
    manager.ssh.validation_failure = failure

    with pytest.raises(TransactionError):
        recover_pending_transaction(manager, stabilize_sec=0)

    assert "validate_journal" in manager.events
    assert "recover_restore" not in manager.events


def test_journal_validation_checks_are_ordered_before_manifest_export() -> None:
    manager = FakeSetupManager()
    manager.ssh.scan_entries = ["dirty-1"]
    manager.ssh.validation_manifest = _manifest()

    assert recover_pending_transaction(manager, stabilize_sec=0) is True

    validate = next(
        command for command in manager.ssh.commands if "PIM_JOURNAL_VALIDATE_OK:" in command
    )
    assert FakeSSH._ordered(validate, [
        "[ ! -L", "[ -d", "stat -c '%u:%g:%a'", "0:0:700",
        "-mindepth 1 -maxdepth 1", "0:0:600", "jq -e", "sha256sum", "base64 -w0",
    ])


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
