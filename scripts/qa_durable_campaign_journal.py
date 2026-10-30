"""Offline PIM QA durability ledger; no hardware runner or execution API.

This module records *claimed* phases from a future independently approved
owner-side controller. Evidence SHA256 values are not trusted attestations.
A CLOSED journal only permits human review, never target execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat


ZERO_SHA = "0" * 64
SCHEMA = 1
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PLAN_ID = re.compile(r"[a-z][a-z0-9_-]{0,62}\Z")
_TRANSITIONS = {
    "PREPARED": {"BASELINED"},
    "BASELINED": {"LEASED"},
    "LEASED": {"RESTORE_ARMED"},
    "RESTORE_ARMED": {"RUNNING", "RESTORING", "FAULT_UNKNOWN"},
    "RUNNING": {"RESTORING", "FAULT_UNKNOWN"},
    "RESTORING": {"RESTORE_CHECKED", "FAULT_UNKNOWN"},
    "RESTORE_CHECKED": {"CLOSED", "FAULT_UNKNOWN"},
    "FAULT_UNKNOWN": {"RESTORING"},
    "CLOSED": set(),
}
_NEEDS_RESTORE = {"RESTORE_ARMED", "RUNNING", "RESTORING",
                  "RESTORE_CHECKED", "FAULT_UNKNOWN"}


class JournalError(RuntimeError):
    """Unsafe or ambiguous durable journal state; fail closed."""


@dataclass(frozen=True)
class CampaignContract:
    run_id: str
    source_sha: str
    baseline_sha256: str
    board_id: str
    target: str
    owner_plan_id: str
    campaign_seconds: int
    restore_seconds: int
    lease_seconds: int

    def __post_init__(self) -> None:
        if (type(self.run_id) is not str or HEX32.fullmatch(self.run_id) is None
                or type(self.source_sha) is not str
                or HEX40.fullmatch(self.source_sha) is None
                or type(self.baseline_sha256) is not str
                or HEX64.fullmatch(self.baseline_sha256) is None
                or type(self.owner_plan_id) is not str
                or PLAN_ID.fullmatch(self.owner_plan_id) is None):
            raise ValueError("INVALID_CAMPAIGN_IDENTITY")
        if self.board_id != "pim" or self.target != "192.168.214.4":
            raise ValueError("UNEXPECTED_TARGET")
        if (type(self.campaign_seconds) is not int
                or self.campaign_seconds != 2700
                or type(self.restore_seconds) is not int
                or self.restore_seconds < 1860
                or type(self.lease_seconds) is not int
                or self.lease_seconds < (
                    self.campaign_seconds + self.restore_seconds + 60
                )):
            raise ValueError("INSUFFICIENT_CAMPAIGN_BUDGET")


def _private_dir(path: Path) -> int:
    if type(path) is not Path:
        path = Path(path)
    try:
        before = path.lstat()
        if (not stat.S_ISDIR(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o700):
            raise JournalError("UNTRUSTED_JOURNAL_DIRECTORY")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY |
                     os.O_NOFOLLOW | os.O_CLOEXEC)
        after = os.fstat(fd)
        if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            os.close(fd)
            raise JournalError("DIRECTORY_CHANGED")
        return fd
    except (OSError, ValueError) as exc:
        raise JournalError("UNTRUSTED_JOURNAL_DIRECTORY") from exc


def _safe_file(fd: int) -> None:
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise JournalError("UNTRUSTED_JOURNAL_FILE")


def _lock(fd: int, *, exclusive: bool) -> None:
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) |
                    fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise JournalError("ACTIVE_JOURNAL") from exc
    except OSError as exc:
        raise JournalError("JOURNAL_LOCK_FAILED") from exc


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _record(payload: dict) -> dict:
    return {**payload, "record_sha256": _sha(_canonical(payload))}


def _valid_hash(value: object) -> bool:
    return type(value) is str and HEX64.fullmatch(value) is not None


def _validate_step(previous: str, current: str, proof: str | None,
                   restored: str | None, contract: CampaignContract) -> None:
    if type(previous) is not str or type(current) is not str:
        raise JournalError("CORRUPT_JOURNAL")
    if current not in _TRANSITIONS.get(previous, set()):
        raise JournalError("ILLEGAL_CAMPAIGN_TRANSITION")
    if not _valid_hash(proof):
        raise JournalError("PROOF_DIGEST_REQUIRED")
    if current == "RESTORE_CHECKED":
        if restored != contract.baseline_sha256:
            raise JournalError("BASELINE_NOT_RESTORED")
    elif restored is not None:
        raise JournalError("UNEXPECTED_RESTORE_DIGEST")


def _load(fd: int) -> tuple[CampaignContract, list[dict]]:
    _safe_file(fd)
    size = os.fstat(fd).st_size
    if size < 1 or size > MAX_JOURNAL_BYTES:
        raise JournalError("CORRUPT_JOURNAL")
    os.lseek(fd, 0, os.SEEK_SET)
    raw = bytearray()
    while len(raw) < size:
        part = os.read(fd, min(size - len(raw), 65536))
        if not part:
            raise JournalError("CORRUPT_JOURNAL")
        raw.extend(part)
    if not raw.endswith(b"\n"):
        raise JournalError("CORRUPT_JOURNAL")
    try:
        lines = raw.decode("utf-8").splitlines()
        events = [json.loads(line) for line in lines]
    except (UnicodeError, ValueError, TypeError) as exc:
        raise JournalError("CORRUPT_JOURNAL") from exc
    if not events or any(type(x) is not dict for x in events):
        raise JournalError("CORRUPT_JOURNAL")

    prev_sha = ZERO_SHA
    contract = None
    required = {"schema", "sequence", "phase", "utc", "previous_sha256",
                "proof_sha256", "baseline_after_sha256", "contract",
                "record_sha256"}
    for index, event in enumerate(events):
        if (set(event) != required or event["schema"] != SCHEMA
                or type(event["sequence"]) is not int
                or event["sequence"] != index
                or event["previous_sha256"] != prev_sha
                or type(event["record_sha256"]) is not str):
            raise JournalError("CORRUPT_JOURNAL")
        digest = event["record_sha256"]
        payload = {k: v for k, v in event.items() if k != "record_sha256"}
        try:
            valid = _sha(_canonical(payload)) == digest
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise JournalError("CORRUPT_JOURNAL")
        try:
            timestamp = dt.datetime.fromisoformat(event["utc"])
            if timestamp.tzinfo is None:
                raise ValueError("NAIVE_JOURNAL_TIMESTAMP")
        except (TypeError, ValueError) as exc:
            raise JournalError("CORRUPT_JOURNAL") from exc

        if index == 0:
            if (type(event["contract"]) is not dict
                    or event["phase"] != "PREPARED"
                    or event["proof_sha256"] is not None
                    or event["baseline_after_sha256"] is not None):
                raise JournalError("CORRUPT_JOURNAL")
            try:
                contract = CampaignContract(**event["contract"])
            except (ValueError, TypeError) as exc:
                raise JournalError("CORRUPT_JOURNAL") from exc
        else:
            if event["contract"] is not None:
                raise JournalError("CORRUPT_JOURNAL")
            assert contract is not None
            _validate_step(events[index - 1]["phase"], event["phase"],
                           event["proof_sha256"],
                           event["baseline_after_sha256"], contract)
        prev_sha = digest
    assert contract is not None
    return contract, events


def _open_existing(directory: Path, name: str, *, exclusive: bool) -> int:
    if type(name) is not str or HEX32.fullmatch(name) is None:
        raise JournalError("INVALID_RUN_ID")
    path = directory / (name + ".jsonl")
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise JournalError("UNTRUSTED_JOURNAL_FILE") from exc
    try:
        _safe_file(fd)
        _lock(fd, exclusive=exclusive)
        return fd
    except Exception:
        os.close(fd)
        raise


def _scan(directory: Path) -> dict[str, object]:
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return {"decision": "BLOCK",
                "reason_codes": ("UNTRUSTED_DIRECTORY_ENTRY",),
                "execution_authorized": False}
    reasons = []
    for entry in entries:
        if (not entry.name.endswith(".jsonl")
                or HEX32.fullmatch(entry.name[:-6]) is None):
            reasons.append("UNTRUSTED_DIRECTORY_ENTRY")
            continue
        fd = None
        try:
            fd = _open_existing(directory, entry.name[:-6], exclusive=False)
            _contract, events = _load(fd)
            if events[-1]["phase"] != "CLOSED":
                reasons.append("PENDING_JOURNAL")
        except JournalError as exc:
            reasons.append("ACTIVE_JOURNAL" if str(exc) == "ACTIVE_JOURNAL"
                           else "CORRUPT_JOURNAL")
        finally:
            if fd is not None:
                os.close(fd)
    return {"decision": "BLOCK" if reasons else "REVIEW_REQUIRED",
            "reason_codes": tuple(dict.fromkeys(reasons)),
            "execution_authorized": False}


def inspect_admission(directory: Path) -> dict[str, object]:
    """Read-only conservative gate. No result ever permits execution."""
    directory = Path(directory)
    try:
        dir_fd = _private_dir(directory)
    except JournalError:
        return {"decision": "BLOCK",
                "reason_codes": ("UNTRUSTED_JOURNAL_DIRECTORY",),
                "execution_authorized": False}
    try:
        try:
            _lock(dir_fd, exclusive=False)
            return _scan(directory)
        except JournalError:
            return {"decision": "BLOCK",
                    "reason_codes": ("ACTIVE_JOURNAL",),
                    "execution_authorized": False}
    finally:
        os.close(dir_fd)


def assess_execution_budget(
    *, host_limit_seconds: int, campaign_seconds: int,
    restore_seconds: int, lease_seconds: int,
    durable_owner_service_verified: bool,
) -> dict[str, object]:
    values = (host_limit_seconds, campaign_seconds, restore_seconds, lease_seconds)
    if any(type(x) is not int or x <= 0 for x in values):
        raise ValueError("INVALID_EXECUTION_BUDGET")
    if type(durable_owner_service_verified) is not bool:
        raise ValueError("INVALID_SERVICE_ATTESTATION")
    reasons = []
    if campaign_seconds != 2700 or restore_seconds < 1860:
        reasons.append("UNSUPPORTED_CAMPAIGN_BUDGET")
    if lease_seconds < campaign_seconds + restore_seconds + 60:
        reasons.append("LEASE_CLEANUP_MARGIN_INSUFFICIENT")
    if not durable_owner_service_verified:
        reasons.append("OWNER_SERVICE_UNVERIFIED")
        if host_limit_seconds < campaign_seconds + restore_seconds:
            reasons.append("HOST_TIMEOUT_CAN_INTERRUPT_RECOVERY")
    return {
        "decision": "BLOCK" if reasons else "REVIEW_REQUIRED",
        "reason_codes": tuple(reasons), "execution_authorized": False,
        "campaign_seconds": campaign_seconds,
        "restore_seconds": restore_seconds, "lease_seconds": lease_seconds,
    }


class CampaignJournal:
    """Single-writer, append-only, fsynced journal. No hardware methods."""

    def __init__(self, fd: int, contract: CampaignContract,
                 records: list[dict], path: Path):
        self._fd = fd
        self.contract = contract
        self._records = records
        self._path = path
        self._closed = False

    def _verify_live_inode_and_chain(self) -> None:
        """Reject a replaced/unlinked file or an out-of-band modification."""
        _safe_file(self._fd)
        try:
            path_info = self._path.lstat()
        except OSError as exc:
            raise JournalError("JOURNAL_PATH_LOST") from exc
        fd_info = os.fstat(self._fd)
        if (not stat.S_ISREG(path_info.st_mode)
                or (path_info.st_dev, path_info.st_ino)
                != (fd_info.st_dev, fd_info.st_ino)):
            raise JournalError("JOURNAL_PATH_CHANGED")
        contract, records = _load(self._fd)
        if (contract != self.contract or len(records) != len(self._records)
                or records[-1]["record_sha256"]
                != self._records[-1]["record_sha256"]):
            raise JournalError("JOURNAL_MODIFIED_OUTSIDE_OWNER")

    @classmethod
    def create(cls, directory: Path, contract: CampaignContract) -> "CampaignJournal":
        if type(contract) is not CampaignContract:
            raise JournalError("INVALID_CAMPAIGN_CONTRACT")
        directory = Path(directory)
        dir_fd = _private_dir(directory)
        try:
            _lock(dir_fd, exclusive=True)
            state = _scan(directory)
            if state["decision"] != "REVIEW_REQUIRED":
                raise JournalError("EXISTING_CAMPAIGN_BLOCKS_NEW_RUN")
            path = directory / (contract.run_id + ".jsonl")
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            except OSError as exc:
                raise JournalError("UNTRUSTED_JOURNAL_FILE") from exc
            try:
                _safe_file(fd)
                _lock(fd, exclusive=True)
                genesis = _record({
                    "schema": SCHEMA, "sequence": 0, "phase": "PREPARED",
                    "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "previous_sha256": ZERO_SHA, "proof_sha256": None,
                    "baseline_after_sha256": None, "contract": asdict(contract),
                })
                _append(fd, genesis)
                os.fsync(dir_fd)
                return cls(fd, contract, [genesis], path)
            except Exception:
                os.close(fd)
                # Keep incomplete journal for forensic review; never delete it.
                raise
        finally:
            os.close(dir_fd)

    @classmethod
    def open(cls, directory: Path, run_id: str) -> "CampaignJournal":
        directory = Path(directory)
        dir_fd = _private_dir(directory)
        try:
            fd = _open_existing(directory, run_id, exclusive=True)
            try:
                contract, events = _load(fd)
                if contract.run_id != run_id:
                    raise JournalError("CORRUPT_JOURNAL")
                return cls(fd, contract, events, directory / (run_id + ".jsonl"))
            except Exception:
                os.close(fd)
                raise
        finally:
            os.close(dir_fd)

    @property
    def phase(self) -> str:
        return self._records[-1]["phase"]

    @property
    def requires_restore(self) -> bool:
        return self.phase in _NEEDS_RESTORE

    @property
    def execution_authorized(self) -> bool:
        return False

    def advance(self, phase: str, *, proof_sha256: str,
                baseline_after_sha256: str | None = None) -> None:
        if self._closed:
            raise JournalError("JOURNAL_CLOSED")
        self._verify_live_inode_and_chain()
        _validate_step(self.phase, phase, proof_sha256,
                       baseline_after_sha256, self.contract)
        row = _record({
            "schema": SCHEMA,
            "sequence": len(self._records),
            "phase": phase,
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "previous_sha256": self._records[-1]["record_sha256"],
            "proof_sha256": proof_sha256,
            "baseline_after_sha256": baseline_after_sha256,
            "contract": None,
        })
        _append(self._fd, row)
        self._records.append(row)

    def close(self) -> None:
        if not self._closed:
            os.close(self._fd)
            self._closed = True

    def __enter__(self) -> "CampaignJournal":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _append(fd: int, event: dict) -> None:
    data = (_canonical(event) + "\n").encode("utf-8")
    if os.fstat(fd).st_size + len(data) > MAX_JOURNAL_BYTES:
        raise JournalError("JOURNAL_SIZE_LIMIT")
    os.lseek(fd, 0, os.SEEK_END)
    offset = 0
    while offset < len(data):
        n = os.write(fd, data[offset:])
        if n <= 0:
            raise JournalError("JOURNAL_WRITE_FAILED")
        offset += n
    os.fsync(fd)
