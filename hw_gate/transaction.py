"""Strict crash-recoverable hardware mutation transaction."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import shlex
from enum import Enum
from typing import Any, Dict, Optional

from setup import EDGECONF_PATH


JOURNAL_ROOT = "/root/shared_v/pim-check-recovery"
_ORIGINAL_NAME = "edgeconf_pim.json.original"
_MANIFEST_NAME = "manifest.json"
_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PERSISTED_STATES = {"JOURNALED", "APPLIED", "REBOOTED", "RESTORED", "VERIFIED", "CLOSED"}
_MANIFEST_KEYS = {
    "schema_version", "run_id", "config_path", "original_sha256", "created_at", "state",
}


class TransactionState(Enum):
    NEW = "NEW"
    SNAPSHOTTED = "SNAPSHOTTED"
    JOURNALED = "JOURNALED"
    APPLIED = "APPLIED"
    REBOOTED = "REBOOTED"
    RESTORED = "RESTORED"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


class TransactionError(RuntimeError):
    """Fail-closed transaction error suitable for an ERROR evidence verdict."""

    verdict = "ERROR"


class TransactionRestorationError(TransactionError):
    """Restoration, readiness, hash verification, or journal closure failed."""


def _quote(value: str) -> str:
    return shlex.quote(value)


def _sanitize_run_id(run_id: str) -> str:
    if not isinstance(run_id, str):
        raise TransactionError("run ID must be a string")
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id.strip()).strip(".-_")[:64]
    if not sanitized or not _SAFE_RUN_ID_RE.fullmatch(sanitized):
        raise TransactionError("run ID is empty after sanitization")
    return sanitized


def _validate_config_path(conf_path: str) -> None:
    if conf_path != EDGECONF_PATH:
        raise TransactionError("config path is outside the strict transaction allowlist")


def _journal_paths(run_id: str) -> Dict[str, str]:
    directory = "{0}/{1}".format(JOURNAL_ROOT, run_id)
    return {
        "directory": directory,
        "original": "{0}/{1}".format(directory, _ORIGINAL_NAME),
        "manifest": "{0}/{1}".format(directory, _MANIFEST_NAME),
    }


def _root_integrity_commands(*, create: bool) -> str:
    root = _quote(JOURNAL_ROOT)
    create_commands = ""
    if create:
        create_commands = (
            "if [ ! -e {root} ]; then [ ! -L {root} ]; mkdir {root}; "
            "chown 0:0 {root}; chmod 700 {root}; fi; "
        ).format(root=root)
    return (
        create_commands
        + "[ ! -L {root} ]; [ -d {root} ]; "
        "[ \"$(stat -c '%u:%g:%a' {root})\" = 0:0:700 ]; "
    ).format(root=root)


def _owned_path_commands(path: str, *, mode: int, directory: bool) -> str:
    quoted = _quote(path)
    type_test = "-d" if directory else "-f"
    return (
        "[ ! -L {path} ]; [ {type_test} {path} ]; "
        "[ \"$(stat -c '%u:%g:%a' {path})\" = 0:0:{mode} ]; "
    ).format(path=quoted, type_test=type_test, mode=mode)


def _exact_entry_count_commands(directory: str, count: int, label: str) -> str:
    quoted = _quote(directory)
    template = _quote("/tmp/pim-journal-count-{0}.XXXXXX".format(label))
    return (
        "count_file=$(mktemp {template}); "
        "find {directory} -mindepth 1 -maxdepth 1 -printf x > \"$count_file\"; "
        "[ \"$(wc -c < \"$count_file\")\" -eq {count} ]; rm -f \"$count_file\"; "
    ).format(directory=quoted, template=template, count=count)


def _has_marker(output: Optional[str], marker: str) -> bool:
    return bool(output) and output.strip().splitlines()[-1] == marker


def _run_remote(manager: object, command: str, stage: str) -> Optional[str]:
    try:
        return manager.ssh.run(command)  # type: ignore[attr-defined]
    except Exception as exc:
        raise TransactionError("remote {0} failed: {1}".format(stage, exc)) from exc


def _run_restoration_remote(manager: object, command: str, stage: str) -> Optional[str]:
    try:
        return manager.ssh.run(command)  # type: ignore[attr-defined]
    except BaseException as exc:
        raise TransactionRestorationError(
            "remote restoration {0} failed: {1}".format(stage, exc)
        ) from exc


def _scan_journals(manager: object) -> list[str]:
    root = _quote(JOURNAL_ROOT)
    prefix = "PIM_JOURNAL_SCAN_OK:"
    command = (
        "set -eu; "
        + _root_integrity_commands(create=True)
        + "scan_file=$(mktemp /tmp/pim-journal-scan.XXXXXX); "
        "trap 'rm -f \"$scan_file\"' EXIT HUP INT TERM; "
        "find {root} -mindepth 1 -maxdepth 1 -print0 > \"$scan_file\"; "
        "payload=$(base64 -w0 \"$scan_file\"); rm -f \"$scan_file\"; "
        "trap - EXIT HUP INT TERM; "
        "printf '{prefix}%s\\n' \"$payload\""
    ).format(root=root, prefix=prefix)
    output = _run_remote(manager, command, "journal scan")
    if not output or len(output.strip().splitlines()) != 1 or not output.startswith(prefix):
        raise TransactionError("journal scan did not complete")
    encoded = output.strip()[len(prefix):]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise TransactionError("journal scan output is malformed") from exc
    if raw and not raw.endswith(b"\0"):
        raise TransactionError("journal scan output is truncated")
    raw_paths = raw[:-1].split(b"\0") if raw else []
    entries = []
    expected_prefix = (JOURNAL_ROOT + "/").encode("ascii")
    for raw_path in raw_paths:
        if not raw_path.startswith(expected_prefix):
            raise TransactionError("journal scan path is outside the recovery root")
        raw_name = raw_path[len(expected_prefix):]
        try:
            entry = raw_name.decode("ascii")
        except UnicodeDecodeError as exc:
            raise TransactionError("journal scan directory name is malformed") from exc
        if not _SAFE_RUN_ID_RE.fullmatch(entry):
            raise TransactionError("journal scan directory name is malformed")
        entries.append(entry)
    if len(entries) != len(set(entries)):
        raise TransactionError("journal scan output is ambiguous")
    return entries


def _decode_manifest(encoded: str, entry: str, conf_path: str) -> Dict[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransactionError("malformed journal manifest") from exc
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise TransactionError("malformed journal manifest fields")
    if manifest.get("schema_version") != 1:
        raise TransactionError("unsupported journal schema")
    if manifest.get("run_id") != entry:
        raise TransactionError("journal run ID does not match its directory")
    if manifest.get("config_path") != conf_path:
        raise TransactionError("journal config path does not match the recovery target")
    digest = manifest.get("original_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise TransactionError("journal original SHA256 is malformed")
    state = manifest.get("state")
    if not isinstance(state, str) or state not in _PERSISTED_STATES:
        raise TransactionError("journal transaction state is malformed")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise TransactionError("journal creation timestamp is malformed")
    try:
        parsed = dt.datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as exc:
        raise TransactionError("journal creation timestamp is malformed") from exc
    if parsed.tzinfo is None:
        raise TransactionError("journal creation timestamp is malformed")
    return manifest


def _load_valid_journal(manager: object, entry: str, conf_path: str) -> Dict[str, Any]:
    if not _SAFE_RUN_ID_RE.fullmatch(entry):
        raise TransactionError("malformed journal directory name")
    paths = _journal_paths(entry)
    directory = _quote(paths["directory"])
    original = _quote(paths["original"])
    manifest = _quote(paths["manifest"])
    command = (
        "set -eu; "
        + _root_integrity_commands(create=False)
        + _owned_path_commands(paths["directory"], mode=700, directory=True)
        + _exact_entry_count_commands(paths["directory"], 2, entry)
        + _owned_path_commands(paths["original"], mode=600, directory=False)
        + _owned_path_commands(paths["manifest"], mode=600, directory=False)
        + "jq -e 'type == \"object\" and "
        "(keys == [\"config_path\",\"created_at\",\"original_sha256\",\"run_id\","
        "\"schema_version\",\"state\"])' {manifest} >/dev/null; "
        "expected=$(jq -er '.original_sha256' {manifest}); "
        "actual=$(sha256sum {original} | awk '{{print $1}}'); [ \"$actual\" = \"$expected\" ]; "
        "payload=$(base64 -w0 {manifest}); printf 'PIM_JOURNAL_VALIDATE_OK:%s\\n' \"$payload\""
    ).format(directory=directory, original=original, manifest=manifest)
    output = _run_remote(manager, command, "journal validation")
    prefix = "PIM_JOURNAL_VALIDATE_OK:"
    if not output or len(output.strip().splitlines()) != 1 or not output.strip().startswith(prefix):
        raise TransactionError("journal validation failed")
    return _decode_manifest(output.strip()[len(prefix):], entry, conf_path)


def _write_manifest_state(
    manager: object, run_id: str, current: str, target: str, *, restoration: bool,
) -> None:
    paths = _journal_paths(run_id)
    manifest = _quote(paths["manifest"])
    temporary = _quote(paths["manifest"] + ".tmp")
    marker = "PIM_JOURNAL_STATE_OK"
    command = (
        "set -eu; "
        + _root_integrity_commands(create=False)
        + _owned_path_commands(paths["directory"], mode=700, directory=True)
        + _owned_path_commands(paths["original"], mode=600, directory=False)
        + _owned_path_commands(paths["manifest"], mode=600, directory=False)
        + "jq -e --arg current {current} '.state == $current' {manifest} >/dev/null; "
        "jq --arg state {target} '.state = $state' {manifest} > {temporary}; "
        "jq -e --arg state {target} '.state == $state and length == 6' {temporary} >/dev/null; "
        "chown 0:0 {temporary}; chmod 600 {temporary}; "
        + _owned_path_commands(paths["manifest"] + ".tmp", mode=600, directory=False)
        + "mv {temporary} {manifest}; "
        + _owned_path_commands(paths["manifest"], mode=600, directory=False)
        + _exact_entry_count_commands(paths["directory"], 2, run_id)
        + "sync; echo {marker}"
    ).format(
        current=_quote(current), target=_quote(target), manifest=manifest,
        temporary=temporary, marker=marker,
    )
    if restoration:
        output = _run_restoration_remote(manager, command, "journal state update")
        if not _has_marker(output, marker):
            raise TransactionRestorationError("could not persist restoration state {0}".format(target))
    else:
        output = _run_remote(manager, command, "journal state update")
        if not _has_marker(output, marker):
            raise TransactionError("could not persist transaction state {0}".format(target))


def _read_config_sha(manager: object, conf_path: str) -> str:
    marker = "PIM_CONFIG_HASH_OK"
    command = (
        "set -eu; digest=$(sha256sum {path} | awk '{{print $1}}'); "
        "printf '%s\\n' \"$digest\"; echo {marker}"
    ).format(path=_quote(conf_path), marker=marker)
    output = _run_restoration_remote(manager, command, "config hash verification")
    if not _has_marker(output, marker):
        raise TransactionRestorationError("restored config hash command failed")
    assert output is not None
    lines = output.strip().splitlines()
    if len(lines) != 2 or not _SHA256_RE.fullmatch(lines[0]):
        raise TransactionRestorationError("restored config hash output is malformed")
    return lines[0]


def _delete_verified_journal(manager: object, run_id: str, conf_path: str, original_sha: str) -> None:
    paths = _journal_paths(run_id)
    marker = "PIM_JOURNAL_DELETE_OK"
    command = (
        "set -eu; "
        + _root_integrity_commands(create=False)
        + _owned_path_commands(paths["directory"], mode=700, directory=True)
        + _owned_path_commands(paths["original"], mode=600, directory=False)
        + _owned_path_commands(paths["manifest"], mode=600, directory=False)
        + _exact_entry_count_commands(paths["directory"], 2, run_id)
        + "jq -e '.state == \"CLOSED\"' {manifest} >/dev/null; "
        "journal_sha=$(sha256sum {original} | awk '{{print $1}}'); "
        "[ \"$journal_sha\" = {expected} ]; "
        "actual=$(sha256sum {config} | awk '{{print $1}}'); [ \"$actual\" = {expected} ]; "
        "rm -f {original} {manifest}; rmdir {directory}; sync; echo {marker}"
    ).format(
        manifest=_quote(paths["manifest"]), config=_quote(conf_path),
        expected=_quote(original_sha), original=_quote(paths["original"]),
        directory=_quote(paths["directory"]), marker=marker,
    )
    output = _run_restoration_remote(manager, command, "journal deletion")
    if not _has_marker(output, marker):
        raise TransactionRestorationError("verified journal deletion failed")


def _restore_from_journal(manager: object, run_id: str, conf_path: str, original_sha: str) -> None:
    paths = _journal_paths(run_id)
    temporary = "{0}.pim-recover-{1}.tmp".format(conf_path, run_id)
    marker = "PIM_JOURNAL_RESTORE_OK"
    command = (
        "set -eu; "
        + _root_integrity_commands(create=False)
        + _owned_path_commands(paths["directory"], mode=700, directory=True)
        + _owned_path_commands(paths["original"], mode=600, directory=False)
        + _owned_path_commands(paths["manifest"], mode=600, directory=False)
        + _exact_entry_count_commands(paths["directory"], 2, run_id)
        + "cp {original} {temporary}; chown 0:0 {temporary}; chmod 600 {temporary}; "
        + _owned_path_commands(temporary, mode=600, directory=False)
        + "jq -e . {temporary} >/dev/null; "
        "actual=$(sha256sum {temporary} | awk '{{print $1}}'); [ \"$actual\" = {expected} ]; "
        "mv {temporary} {config}; sync; echo {marker}"
    ).format(
        original=_quote(paths["original"]), temporary=_quote(temporary),
        expected=_quote(original_sha), config=_quote(conf_path), marker=marker,
    )
    output = _run_restoration_remote(manager, command, "persistent original restore")
    if not _has_marker(output, marker):
        raise TransactionRestorationError("persistent original restore failed")


def _reboot_for_restoration(manager: object, stabilize_sec: int, stage: str) -> None:
    try:
        manager.reboot_and_wait(stabilize_sec=stabilize_sec)  # type: ignore[attr-defined]
    except BaseException as exc:
        raise TransactionRestorationError("{0} failed: {1}".format(stage, exc)) from exc


def recover_pending_transaction(
    setup_manager: object, *, conf_path: str = EDGECONF_PATH, stabilize_sec: int = 30,
) -> bool:
    """Restore one validated dirty journal; fail closed on ambiguity or errors."""
    _validate_config_path(conf_path)
    entries = _scan_journals(setup_manager)
    if not entries:
        return False
    if len(entries) != 1:
        raise TransactionError("multiple recovery journals block target access")
    entry = entries[0]
    manifest = _load_valid_journal(setup_manager, entry, conf_path)
    original_sha = manifest["original_sha256"]
    current_state = manifest["state"]
    assert isinstance(original_sha, str) and isinstance(current_state, str)
    _restore_from_journal(setup_manager, entry, conf_path, original_sha)
    _write_manifest_state(
        setup_manager, entry, current_state, TransactionState.RESTORED.value, restoration=True,
    )
    _reboot_for_restoration(setup_manager, stabilize_sec, "recovery reboot/readiness")
    if _read_config_sha(setup_manager, conf_path) != original_sha:
        raise TransactionRestorationError("recovered config SHA256 mismatch")
    _write_manifest_state(
        setup_manager, entry, TransactionState.RESTORED.value,
        TransactionState.VERIFIED.value, restoration=True,
    )
    _write_manifest_state(
        setup_manager, entry, TransactionState.VERIFIED.value,
        TransactionState.CLOSED.value, restoration=True,
    )
    _delete_verified_journal(setup_manager, entry, conf_path, original_sha)
    return True


class StrictHardwareTransaction:
    """Apply config only inside a durable, exactly-restored transaction."""

    def __init__(
        self,
        setup_manager: object,
        run_id: str,
        changes: Optional[dict] = None,
        *,
        conf_path: str = EDGECONF_PATH,
        stabilize_sec: int = 30,
    ) -> None:
        _validate_config_path(conf_path)
        if changes is not None and (not isinstance(changes, dict) or not changes):
            raise TransactionError("changes must be a non-empty mapping when supplied")
        if isinstance(stabilize_sec, bool) or not isinstance(stabilize_sec, int) or stabilize_sec < 0:
            raise TransactionError("stabilize_sec must be a non-negative integer")
        self.setup_manager = setup_manager
        self.run_id = _sanitize_run_id(run_id)
        self.conf_path = conf_path
        self.stabilize_sec = stabilize_sec
        self._initial_changes = changes
        self._state = TransactionState.NEW
        self._terminal_state = TransactionState.NEW
        self._state_history = [TransactionState.NEW]
        self._manifest: Dict[str, Any] = {}
        self._original_sha = ""
        self._entered = False
        self._restoring = False
        self._journal_deleted = False

    @property
    def state(self) -> TransactionState:
        return self._state

    @property
    def terminal_state(self) -> TransactionState:
        return self._terminal_state

    @property
    def state_history(self) -> tuple[TransactionState, ...]:
        return tuple(self._state_history)

    @property
    def manifest(self) -> Dict[str, Any]:
        return dict(self._manifest)

    def _record_state(self, state: TransactionState) -> None:
        self._state = state
        self._terminal_state = state
        if self._state_history[-1] is not state:
            self._state_history.append(state)

    def _record_error(self) -> None:
        self._terminal_state = TransactionState.ERROR
        if self._state_history[-1] is not TransactionState.ERROR:
            self._state_history.append(TransactionState.ERROR)

    def _persist_journal(self, snapshot_payload: str) -> None:
        try:
            original = base64.b64decode(snapshot_payload, validate=True)
            json.loads(original.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransactionError("validated snapshot payload is unusable") from exc
        self._original_sha = hashlib.sha256(original).hexdigest()
        self._manifest = {
            "schema_version": 1,
            "run_id": self.run_id,
            "config_path": self.conf_path,
            "original_sha256": self._original_sha,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
            "state": TransactionState.JOURNALED.value,
        }
        encoded_manifest = base64.b64encode(
            json.dumps(self._manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        paths = _journal_paths(self.run_id)
        original_tmp = paths["original"] + ".tmp"
        manifest_tmp = paths["manifest"] + ".tmp"
        marker = "PIM_JOURNAL_PERSIST_OK"
        command = (
            "set -eu; umask 077; "
            + _root_integrity_commands(create=True)
            + "[ ! -e {directory} ]; [ ! -L {directory} ]; "
            "mkdir {directory}; chown 0:0 {directory}; chmod 700 {directory}; "
            + _owned_path_commands(paths["directory"], mode=700, directory=True)
            +
            "printf '%s' {snapshot} | base64 -d > {original_tmp}; "
            "chown 0:0 {original_tmp}; chmod 600 {original_tmp}; "
            + _owned_path_commands(original_tmp, mode=600, directory=False)
            + "jq -e . {original_tmp} >/dev/null; "
            "actual=$(sha256sum {original_tmp} | awk '{{print $1}}'); "
            "[ \"$actual\" = {expected} ]; mv {original_tmp} {original}; "
            "printf '%s' {manifest_payload} | base64 -d > {manifest_tmp}; "
            "chown 0:0 {manifest_tmp}; chmod 600 {manifest_tmp}; "
            + _owned_path_commands(manifest_tmp, mode=600, directory=False)
            +
            "jq -e 'type == \"object\" and length == 6 and .state == \"JOURNALED\"' "
            "{manifest_tmp} >/dev/null; mv {manifest_tmp} {manifest}; "
            + _owned_path_commands(paths["original"], mode=600, directory=False)
            + _owned_path_commands(paths["manifest"], mode=600, directory=False)
            + _exact_entry_count_commands(paths["directory"], 2, self.run_id)
            + "sync; echo {marker}"
        ).format(
            directory=_quote(paths["directory"]),
            snapshot=_quote(snapshot_payload), original_tmp=_quote(original_tmp),
            expected=_quote(self._original_sha), original=_quote(paths["original"]),
            manifest_payload=_quote(encoded_manifest), manifest_tmp=_quote(manifest_tmp),
            manifest=_quote(paths["manifest"]), marker=marker,
        )
        output = _run_remote(self.setup_manager, command, "journal persistence")
        if not _has_marker(output, marker):
            raise TransactionError("persistent journal validation failed")
        self._record_state(TransactionState.JOURNALED)

    def _set_state(self, target: TransactionState, *, restoration: bool = False) -> None:
        current = self._state
        _write_manifest_state(
            self.setup_manager, self.run_id, current.value, target.value,
            restoration=restoration,
        )
        self._record_state(target)
        self._manifest["state"] = target.value

    def _prepare(self) -> None:
        recover_pending_transaction(
            self.setup_manager, conf_path=self.conf_path, stabilize_sec=self.stabilize_sec,
        )
        snapshotted = self.setup_manager.snapshot_config(  # type: ignore[attr-defined]
            self.conf_path
        )
        if not snapshotted:
            raise TransactionError("host snapshot validation failed")
        self._record_state(TransactionState.SNAPSHOTTED)
        payload = self.setup_manager.get_snapshot_payload(  # type: ignore[attr-defined]
            self.conf_path
        )
        if not isinstance(payload, str) or not payload:
            raise TransactionError("validated host snapshot payload is unavailable")
        self._persist_journal(payload)
        backed_up = self.setup_manager.backup(self.conf_path)  # type: ignore[attr-defined]
        if not backed_up:
            raise TransactionError("config-guard backup failed")

    def _raise_after_restoration(self, original: BaseException) -> None:
        try:
            self.restore_and_verify()
        except TransactionRestorationError as restoration:
            self._record_error()
            raise restoration from original
        self._record_error()
        raise original

    def _apply_and_reboot(self, changes: dict) -> None:
        if self._state not in {TransactionState.JOURNALED, TransactionState.REBOOTED}:
            raise TransactionError("transaction is not ready for mutation")
        try:
            self.setup_manager.apply_changes(changes, self.conf_path)  # type: ignore[attr-defined]
            self._set_state(TransactionState.APPLIED)
            self.setup_manager.reboot_and_wait(  # type: ignore[attr-defined]
                stabilize_sec=self.stabilize_sec
            )
            self._set_state(TransactionState.REBOOTED)
        except BaseException as original:
            self._raise_after_restoration(original)

    def apply_and_reboot(self, changes: dict) -> None:
        """Apply one campaign setpoint and return after reboot readiness."""
        if not self._entered:
            raise TransactionError("transaction context has not been entered")
        if not isinstance(changes, dict) or not changes:
            raise TransactionError("changes must be a non-empty mapping")
        self._apply_and_reboot(changes)

    def __enter__(self) -> "StrictHardwareTransaction":
        if self._entered:
            raise TransactionError("transaction context cannot be re-entered")
        try:
            self._prepare()
            self._entered = True
            if self._initial_changes is not None:
                self._apply_and_reboot(self._initial_changes)
        except TransactionError:
            self._record_error()
            raise
        return self

    def restore_and_verify(self) -> None:
        """Idempotently restore, reboot, hash-verify, close, and delete the journal."""
        if self._state is TransactionState.CLOSED and self._journal_deleted:
            return
        if self._state not in {
            TransactionState.JOURNALED, TransactionState.APPLIED, TransactionState.REBOOTED,
            TransactionState.RESTORED, TransactionState.VERIFIED, TransactionState.CLOSED,
        }:
            raise TransactionRestorationError("transaction has no durable restore point")
        if self._restoring:
            return
        self._restoring = True
        try:
            if self._state is TransactionState.CLOSED:
                _delete_verified_journal(
                    self.setup_manager, self.run_id, self.conf_path, self._original_sha,
                )
                self._journal_deleted = True
                return
            if self._state not in {TransactionState.RESTORED, TransactionState.VERIFIED}:
                try:
                    restored = self.setup_manager.restore_from_snapshot(  # type: ignore[attr-defined]
                        self.conf_path
                    )
                except BaseException as exc:
                    raise TransactionRestorationError(
                        "host snapshot restore failed: {0}".format(exc)
                    ) from exc
                if not restored:
                    raise TransactionRestorationError("host snapshot restore failed")
                self._set_state(TransactionState.RESTORED, restoration=True)
            if self._state is TransactionState.RESTORED:
                _reboot_for_restoration(
                    self.setup_manager, self.stabilize_sec, "post-restore reboot/readiness",
                )
                if _read_config_sha(self.setup_manager, self.conf_path) != self._original_sha:
                    raise TransactionRestorationError("restored config SHA256 mismatch")
                self._set_state(TransactionState.VERIFIED, restoration=True)
            if self._state is TransactionState.VERIFIED:
                self._set_state(TransactionState.CLOSED, restoration=True)
                _delete_verified_journal(
                    self.setup_manager, self.run_id, self.conf_path, self._original_sha,
                )
                self._journal_deleted = True
        finally:
            self._restoring = False

    def unwind_for_signal(self) -> None:
        """Use the context manager's restoration path during explicit signal unwinding."""
        try:
            self.restore_and_verify()
        except TransactionRestorationError:
            self._record_error()
            raise
        self._record_error()

    def __exit__(
        self, exc_type: Optional[type], exc: Optional[BaseException], traceback: object,
    ) -> bool:
        del exc_type, traceback
        try:
            self.restore_and_verify()
        except TransactionRestorationError as restoration:
            self._record_error()
            if exc is not None:
                raise restoration from exc
            raise
        if exc is not None:
            self._record_error()
        return False
