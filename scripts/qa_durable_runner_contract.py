"""Offline-only evidence assessment for an owner-controlled durable QA runner.

The contract never starts a service, acquires a lease, or changes a board.
No set of Boolean evidence can authorize execution.
"""
from __future__ import annotations

from dataclasses import dataclass


MIN_CLEANUP_SECONDS = 1860
RECORD_SECONDS = 2700
LEASE_RELEASE_MARGIN_SECONDS = 60


@dataclass(frozen=True)
class RunnerEvidence:
    owner_service_registered: bool | None = None
    journal_fsync_verified: bool | None = None
    recovery_on_controller_loss_verified: bool | None = None
    board_lease_fencing_verified: bool | None = None
    source_pin_verified: bool | None = None
    recorder_closed_verified: bool | None = None
    restore_idempotence_verified: bool | None = None
    final_state_verified: bool | None = None
    cleanup_budget_seconds: int | None = None
    lease_seconds: int | None = None
    supervised_max_seconds: int | None = None


def assess_runner(proof: RunnerEvidence) -> dict[str, object]:
    """Return risk evidence only; never start or authorize a hardware run."""
    if type(proof) is not RunnerEvidence:
        raise ValueError("invalid runner evidence")
    checks = (
        ("owner_service_registered", "OWNER_SERVICE_UNVERIFIED"),
        ("journal_fsync_verified", "DURABLE_JOURNAL_UNVERIFIED"),
        ("recovery_on_controller_loss_verified", "OWNER_LOSS_RECOVERY_UNVERIFIED"),
        ("board_lease_fencing_verified", "LEASE_FENCING_UNVERIFIED"),
        ("source_pin_verified", "SOURCE_PIN_UNVERIFIED"),
        ("recorder_closed_verified", "WRITER_CLOSE_UNVERIFIED"),
        ("restore_idempotence_verified", "RESTORE_IDEMPOTENCE_UNVERIFIED"),
        ("final_state_verified", "FINAL_STATE_UNVERIFIED"),
    )
    for key, _ in checks:
        if getattr(proof, key) not in (True, False, None) or (
                getattr(proof, key) is not None and
                type(getattr(proof, key)) is not bool):
            raise ValueError("invalid runner attestation: " + key)
    for key in ("cleanup_budget_seconds", "lease_seconds",
                "supervised_max_seconds"):
        value = getattr(proof, key)
        if type(value) is not int or value <= 0:
            raise ValueError("invalid runner duration: " + key)

    reasons = [code for key, code in checks if getattr(proof, key) is not True]
    required = RECORD_SECONDS + MIN_CLEANUP_SECONDS
    if proof.cleanup_budget_seconds < MIN_CLEANUP_SECONDS:
        reasons.append("CLEANUP_RESERVE_INSUFFICIENT")
    if proof.supervised_max_seconds < required:
        reasons.append("SUPERVISOR_WINDOW_INSUFFICIENT")
    if proof.lease_seconds < required + LEASE_RELEASE_MARGIN_SECONDS:
        reasons.append("LEASE_RESERVE_INSUFFICIENT")
    return {
        "decision": "BLOCK" if reasons else "REVIEW_REQUIRED",
        "execution_authorized": False,
        "required_seconds": required,
        "lease_required_seconds": required + LEASE_RELEASE_MARGIN_SECONDS,
        "reason_codes": tuple(reasons),
        "evidence_only": True,
    }
