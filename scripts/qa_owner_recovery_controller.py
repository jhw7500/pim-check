"""Fail-closed owner-side restoration reconciler; no embedded board transport.

The future owner-controlled service must supply an independently reviewed
adapter. The adapter alone may acquire PIM lease and recover a validated
on-board journal. This module never opens SSH, deletes files or grants a
new QA mutation. All durable host intent updates precede external recovery.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from scripts.qa_durable_journal import JournalError, JournalStore
from scripts.qa_owner_supervisor import owner_lock, OwnerServiceError

TARGET = "192.168.214.4"
MIN_RESTORE_LEASE_SECONDS = 900
MAX_EVIDENCE_AGE_SECONDS = 30

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA64 = re.compile(r"[0-9a-f]{64}\Z")
_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}\Z")


class RecoveryBlocked(RuntimeError):
    """Do not claim completed restore or start a new campaign."""


@dataclass(frozen=True)
class Baseline:
    run_id: str
    source_sha: str
    edgeconf_sha: str
    ord_conf_sha: str
    runtime_sha: str
    target: str = TARGET

    def __post_init__(self) -> None:
        if (type(self.run_id) is not str or not _RUN.fullmatch(self.run_id)
                or type(self.source_sha) is not str
                or not _SHA40.fullmatch(self.source_sha)
                or any(type(v) is not str or not _SHA64.fullmatch(v)
                       for v in (self.edgeconf_sha, self.ord_conf_sha,
                                 self.runtime_sha))
                or self.target != TARGET):
            raise RecoveryBlocked("invalid immutable owner baseline")


@dataclass(frozen=True)
class BoardEvidence:
    # This witness is supplied by the reviewed adapter and must originate
    # inside the SAME authentic jhw-control board lease.
    target: str
    source_sha: str
    source_attested: bool
    installed_binaries_attested: bool
    exclusive_lease_attested: bool
    lease_session: str
    lease_remaining_seconds: int
    age_seconds: int
    on_board_journal_state: str
    on_board_run_id: str | None
    on_board_original_sha: str | None
    edgeconf_sha: str
    ord_conf_sha: str
    runtime_sha: str
    all_led_off: bool
    consumers_ready: bool
    writer_closed: bool
    owner_idle: bool
    # A momentary FD scan cannot prove persistent writer quiescence.
    writer_fence_session: str | None = None


class BoardRecoveryAdapter(Protocol):
    def observe(self) -> BoardEvidence: ...
    def recover_validated_board_journal(
        self, *, run_id: str, original_sha256: str,
    ) -> None: ...


def _check_common(row: BoardEvidence, base: Baseline) -> None:
    if type(row) is not BoardEvidence:
        raise RecoveryBlocked("missing typed board evidence")
    if row.target != TARGET or row.source_sha != base.source_sha:
        raise RecoveryBlocked("source or target mismatch")
    if (row.source_attested is not True
            or row.installed_binaries_attested is not True
            or row.exclusive_lease_attested is not True):
        raise RecoveryBlocked("owner source, installed binary or official lease unverified")
    if (type(row.lease_session) is not str or not row.lease_session
            or type(row.lease_remaining_seconds) is not int
            or row.lease_remaining_seconds < MIN_RESTORE_LEASE_SECONDS):
        raise RecoveryBlocked("lease has insufficient verified restore time")
    if (type(row.age_seconds) is not int
            or row.age_seconds < 0
            or row.age_seconds > MAX_EVIDENCE_AGE_SECONDS):
        raise RecoveryBlocked("stale or untrusted board observation")
    if row.owner_idle is not True or row.writer_closed is not True:
        raise RecoveryBlocked("owner busy or writer not closed")
    if row.writer_fence_session != row.lease_session:
        raise RecoveryBlocked("writer not persistently fenced by owner lease")
    if row.all_led_off is not True:
        raise RecoveryBlocked("MCP4018-absent board would enable LED")
    if row.ord_conf_sha != base.ord_conf_sha:
        raise RecoveryBlocked("ORD/VCM source diverged from immutable baseline")


def _check_restored(row: BoardEvidence, base: Baseline) -> None:
    _check_common(row, base)
    if (row.on_board_journal_state != "ABSENT"
            or row.on_board_run_id is not None
            or row.on_board_original_sha is not None):
        raise RecoveryBlocked("on-board recovery journal is not closed")
    if row.edgeconf_sha != base.edgeconf_sha:
        raise RecoveryBlocked("edgeconf baseline mismatch")
    if row.runtime_sha != base.runtime_sha:
        raise RecoveryBlocked("runtime baseline mismatch")
    if row.consumers_ready is not True:
        raise RecoveryBlocked("consumer readiness not independently attested")


def _blocked(state: str, reason: str, *, recovery_called: bool = False) -> dict:
    return {
        "state": state,
        "reason": reason,
        "decision": "BLOCK",
        "execution_authorized": False,
        "new_mutation_authorized": False,
        "recovery_callback_called": recovery_called,
        "independent_final_attestation_required": True,
    }


def _reconcile_locked(
    journal: JournalStore,
    baseline: Baseline,
    adapter: BoardRecoveryAdapter,
    *,
    source_checkout_verified: bool = False,
) -> dict:
    """Persist restoration intent before effects; never replay a clean journal.

    Source checkout verification is the independent owner's obligation and
    is not an approval token by itself. If a process dies after a completed
    remote restore but before acknowledgement, the next invocation observes
    the already-restored board and advances without invoking restore twice.
    """
    if type(journal) is not JournalStore or type(baseline) is not Baseline:
        raise RecoveryBlocked("untrusted journal or baseline")
    if source_checkout_verified is not True:
        raise RecoveryBlocked("deployed source not attested")
    try:
        current = journal.read(expected_source_sha=baseline.source_sha)
    except JournalError as exc:
        raise RecoveryBlocked("trusted host journal unavailable") from exc
    if (current["run_id"] != baseline.run_id
            or current["target"] != baseline.target
            or current["baseline_sha256"] != baseline.edgeconf_sha):
        raise RecoveryBlocked("immutable host journal identity mismatch")

    state = current["state"]
    if state in {"PREPARED", "BASELINE_SAVED"}:
        return _blocked(state, "NO_MUTATION_CONFIRMED")
    if state == "FINAL_ATTESTATION_PENDING":
        return _blocked(state, "FINAL_ATTESTATION_REQUIRED")
    if state == "MUTATION_PENDING":
        try:
            current = journal.transition(
                "RESTORE_PENDING", expected_revision=current["revision"])
        except JournalError as exc:
            raise RecoveryBlocked("cannot persist restore intent") from exc
    if current["state"] != "RESTORE_PENDING":
        raise RecoveryBlocked("unknown restoration state")

    before = adapter.observe()
    _check_common(before, baseline)
    if before.on_board_journal_state == "ABSENT":
        _check_restored(before, baseline)
        recovery_called = False
    elif before.on_board_journal_state == "DIRTY":
        if (before.on_board_run_id != baseline.run_id
                or before.on_board_original_sha != baseline.edgeconf_sha):
            raise RecoveryBlocked("untrusted foreign on-board recovery journal")
        # No arbitrary remote config writes or ad-hoc reboot are exposed.
        adapter.recover_validated_board_journal(
            run_id=baseline.run_id, original_sha256=baseline.edgeconf_sha)
        recovery_called = True
        after = adapter.observe()
        if after.lease_session != before.lease_session:
            raise RecoveryBlocked("lease ownership changed during restoration")
        _check_restored(after, baseline)
    else:
        raise RecoveryBlocked("on-board journal identity not verifiable")

    try:
        current = journal.transition(
            "FINAL_ATTESTATION_PENDING", expected_revision=current["revision"])
    except JournalError as exc:
        raise RecoveryBlocked("post-restore host journal transition failed") from exc
    return _blocked(current["state"], "FINAL_ATTESTATION_REQUIRED",
                    recovery_called=recovery_called)


def reconcile(
    journal: JournalStore,
    baseline: Baseline,
    adapter: BoardRecoveryAdapter,
    *,
    source_checkout_verified: bool = False,
) -> dict:
    """Hold the same exclusive lock as the passive owner for ALL remote effects.

    JournalStore's per-record flock is not enough: it releases its lock between
    transitions and remote calls. A second service must never run a competing
    restore while the first one is observing or waiting for the board.
    """
    if type(journal) is not JournalStore:
        raise RecoveryBlocked("untrusted journal")
    try:
        with owner_lock(journal.directory):
            return _reconcile_locked(
                journal, baseline, adapter,
                source_checkout_verified=source_checkout_verified)
    except OwnerServiceError as exc:
        raise RecoveryBlocked("exclusive owner lock unavailable") from exc
