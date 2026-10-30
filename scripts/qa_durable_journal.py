"""Crash-safe, local-host-only intent journal for future PIM owner QA service.

This module stores immutable run identity and ordered intent transitions. It
does not start a supervisor, contact a board, invoke restore or authorize QA.
A separate, reviewed owner service must attest external effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Iterator


class JournalError(RuntimeError):
    """Fail closed on stale, ambiguous, inaccessible or corrupt journal."""


STATES = {
    "PREPARED": "BASELINE_SAVED",
    "BASELINE_SAVED": "MUTATION_PENDING",
    "MUTATION_PENDING": "RESTORE_PENDING",
    "RESTORE_PENDING": "FINAL_ATTESTATION_PENDING",
}
ALLOWED_TARGET = "192.168.214.4"
JOURNAL_NAME = "journal.json"
MAX_JOURNAL_BYTES = 16 * 1024
FIELDS = frozenset({
    "schema", "run_id", "target", "source_sha", "baseline_sha256",
    "revision", "state", "updated_at", "digest",
})


def _validate_identities(run_id: str, source_sha: str,
                         baseline_sha256: str, target: str) -> None:
    if type(run_id) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", run_id) is None:
        raise ValueError("invalid owner QA run identity")
    if type(source_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise ValueError("invalid source SHA pin")
    if (type(baseline_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", baseline_sha256) is None):
        raise ValueError("invalid baseline digest pin")
    if target != ALLOWED_TARGET or type(target) is not str:
        raise ValueError("board target must be exact pinned PIM address")


def _digest(body: dict) -> str:
    b = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(b).hexdigest()


def _status(record: dict) -> dict:
    state = record["state"]
    if state in ("PREPARED", "BASELINE_SAVED"):
        next_action = "ABORT_WITHOUT_MUTATION"
    elif state in ("MUTATION_PENDING", "RESTORE_PENDING"):
        next_action = "RESTORE_REQUIRED"
    else:
        next_action = "INDEPENDENT_FINAL_ATTESTATION_REQUIRED"
    return {
        **record,
        "action": next_action,
        "decision": "BLOCK",
        "execution_authorized": False,
        "evidence_only": True,
    }


class JournalStore:
    """One exclusive active journal per preexisting, owner-controlled directory.

    Exclusive Linux flock serializes cooperating callers. All file operations
    use a trusted directory FD and NOFOLLOW; writes are temp+fsync+rename+
    directory fsync. This is an integrity/recovery ledger, not authentication
    or proof that the corresponding PIM board command occurred.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    @contextmanager
    def _lock(self) -> Iterator[int]:
        dfd = None
        lfd = None
        try:
            dfd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            dstat = os.fstat(dfd)
            if not stat.S_ISDIR(dstat.st_mode) or (dstat.st_mode & 0o022):
                raise JournalError("journal directory is unsafe")
            lfd = os.open(".journal.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                          0o600, dir_fd=dfd)
            lst = os.fstat(lfd)
            if not stat.S_ISREG(lst.st_mode) or lst.st_nlink != 1:
                raise JournalError("journal lock is unsafe")
            fcntl.flock(lfd, fcntl.LOCK_EX)
            yield dfd
        except OSError as exc:
            raise JournalError(f"journal filesystem unavailable: {exc.strerror}") from exc
        finally:
            if lfd is not None:
                os.close(lfd)
            if dfd is not None:
                os.close(dfd)

    def _read_locked(self, dfd: int) -> dict | None:
        fd = None
        try:
            try:
                fd = os.open(JOURNAL_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
            except FileNotFoundError:
                return None
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_JOURNAL_BYTES:
                raise JournalError("journal file type, links or size is invalid")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise JournalError("journal file permission must be owner-only")
            raw = bytearray()
            while True:
                piece = os.read(fd, 4096)
                if not piece:
                    break
                raw.extend(piece)
                if len(raw) > MAX_JOURNAL_BYTES:
                    raise JournalError("journal exceeds maximum size")
            try:
                data = json.loads(raw.decode("utf8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise JournalError("corrupt journal JSON") from exc
            if type(data) is not dict or set(data) != FIELDS or data.get("schema") != 1:
                raise JournalError("untrusted journal schema")
            if type(data["revision"]) is not int or data["revision"] < 0:
                raise JournalError("invalid journal revision")
            if data["state"] not in (*STATES, "FINAL_ATTESTATION_PENDING"):
                raise JournalError("unknown journal phase")
            try:
                _validate_identities(data["run_id"], data["source_sha"],
                                     data["baseline_sha256"], data["target"])
                dt = datetime.fromisoformat(data["updated_at"])
                if dt.tzinfo is None:
                    raise ValueError("missing timezone")
            except (ValueError, TypeError, KeyError) as exc:
                raise JournalError("invalid journal identity or time") from exc
            signed = {key: value for key, value in data.items() if key != "digest"}
            if type(data["digest"]) is not str or not hmac.compare_digest(data["digest"], _digest(signed)):
                raise JournalError("journal integrity digest mismatch")
            return data
        except OSError as exc:
            raise JournalError(f"journal read unavailable: {exc.strerror}") from exc
        finally:
            if fd is not None:
                os.close(fd)

    def _write_locked(self, dfd: int, body: dict) -> None:
        packed = dict(body)
        packed["digest"] = _digest(body)
        data = (json.dumps(packed, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(data) > MAX_JOURNAL_BYTES:
            raise JournalError("journal record too large")
        tmp = ".journal." + secrets.token_hex(12) + ".tmp"
        fd = None
        exists = False
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=dfd)
            exists = True
            remaining = memoryview(data)
            while remaining:
                count = os.write(fd, remaining)
                if count <= 0:
                    raise OSError("journal short write")
                remaining = remaining[count:]
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.replace(tmp, JOURNAL_NAME, src_dir_fd=dfd, dst_dir_fd=dfd)
            exists = False
            os.fsync(dfd)
        except OSError as exc:
            raise JournalError(f"journal atomic persistence failed: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)
            if exists:
                try:
                    os.unlink(tmp, dir_fd=dfd)
                except OSError:
                    pass

    def begin(self, *, run_id: str, source_sha: str, baseline_sha256: str,
              target: str = ALLOWED_TARGET) -> dict:
        _validate_identities(run_id, source_sha, baseline_sha256, target)
        with self._lock() as dfd:
            existing = self._read_locked(dfd)
            if existing is not None:
                raise JournalError("journal already exists; cannot start new campaign")
            record = {
                "schema": 1,
                "run_id": run_id,
                "source_sha": source_sha,
                "baseline_sha256": baseline_sha256,
                "target": target,
                "revision": 0,
                "state": "PREPARED",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write_locked(dfd, record)
            return _status({**record, "digest": _digest(record)})

    def read(self, *, expected_source_sha: str | None = None) -> dict:
        with self._lock() as dfd:
            record = self._read_locked(dfd)
            if record is None:
                raise JournalError("journal missing; cannot claim clean state")
            if expected_source_sha is not None and record["source_sha"] != expected_source_sha:
                raise JournalError("journal source mismatch")
            return _status(record)

    def transition(self, next_state: str, *, expected_revision: int) -> dict:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid expected revision")
        if type(next_state) is not str:
            raise ValueError("invalid next state")
        with self._lock() as dfd:
            current = self._read_locked(dfd)
            if current is None:
                raise JournalError("journal missing; transition denied")
            if current["revision"] != expected_revision:
                raise JournalError("journal revision mismatch; transition blocked")
            if STATES.get(current["state"]) != next_state:
                raise JournalError("illegal journal transition; restore state unknown")
            body = {
                **{k: v for k, v in current.items() if k != "digest"},
                "revision": expected_revision + 1,
                "state": next_state,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write_locked(dfd, body)
            return _status({**body, "digest": _digest(body)})
