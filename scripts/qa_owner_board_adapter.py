"""Source-pinned PIM owner recovery adapter. NOT a runnable board command.

This is a library integration boundary, not an installer, CLI or a service.
No board action is performed on import or construction.

Only an independently audited owner service may instantiate this class.
It must supply a real, trusted BoardInspector that samples the on-board
journal, source/runtime and binary hashes; and an actual SetupManager under
the genuine jhw-control *long* lease. The adapter may invoke the pre-existing
strict on-board journal restoration, but never arbitrary remote commands,
manual file removal, new camera setpoints, or a new fault campaign.

Current hardware inspector, installed durable service and one-use registered
owner command are NOT shipped. An honest evidence-only test cannot claim
that a live board has been restored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import subprocess
import time
from typing import Callable, Mapping, Protocol

from hw_gate.transaction import recover_pending_transaction
from setup import EDGECONF_PATH
from scripts.qa_owner_recovery_controller import (
    Baseline, BoardEvidence, MAX_EVIDENCE_AGE_SECONDS,
    MIN_RESTORE_LEASE_SECONDS, TARGET,
)
from scripts.qa_owner_supervisor import check_source, OwnerServiceError

REQUIRED_BINARIES = (
    "/opt/pim/bin/chk_cam_operate.sh", "gstApp", "ord", "vcm",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PID = re.compile(r"[1-9][0-9]*\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_REASON = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{3,80}\Z")
_ALLOWED_RECOVERY_PHASES = frozenset({"APPLIED", "REBOOTED"})


class AdapterBlocked(RuntimeError):
    """A read/lease/source mismatch must stop recovery without side effects."""


@dataclass(frozen=True)
class VerifiedLongLease:
    session: str
    remaining_seconds: int
    source_sha: str


class OfficialLeaseVerifier:
    """Recheck actual jhw-control owner ancestry and active lease via wrapper.

    The environment marker is a *candidate*, never a lease authorization.
    The pinned with_pim_board.sh --check-held command independently checks:
    ancestor board-with process, exact session, exclusive/alive board status,
    genuine extended lease and active deadline supervisor.
    """

    def __init__(
        self, *, source_root: Path, source_sha: str, expected_until: str,
        purpose: str, env: Mapping[str, str] | None = None,
        runner: Callable | None = None, clock: Callable[[], float] | None = None,
    ) -> None:
        self.source_root = Path(source_root)
        self.source_sha = source_sha
        self.expected_until = expected_until
        self.purpose = purpose
        self.env = dict(os.environ if env is None else env)
        self.runner = subprocess.run if runner is None else runner
        self.clock = time.time if clock is None else clock

    def verify(self) -> VerifiedLongLease:
        try:
            if not _UTC.fullmatch(self.expected_until):
                raise ValueError("invalid UTC deadline")
            if not _REASON.fullmatch(self.purpose):
                raise ValueError("invalid approved purpose")
            deadline = datetime.strptime(
                self.expected_until, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc).timestamp()
            now = self.clock()
            remaining = int(deadline - now)
        except (ValueError, TypeError, OverflowError) as exc:
            raise AdapterBlocked("invalid owner lease deadline") from exc
        if remaining < MIN_RESTORE_LEASE_SECONDS:
            raise AdapterBlocked("insufficient remaining restoration lease")
        owner = self.env.get("PIM_BOARD_LOCK_OWNER_PID")
        session = self.env.get("PIM_BOARD_LOCK_SESSION")
        if (self.env.get("PIM_BOARD_LOCK_HELD") != "1"
                or self.env.get("PIM_BOARD_LOCK_BOARD_ID") != "pim"
                or not isinstance(owner, str) or not _PID.fullmatch(owner)
                or not isinstance(session, str) or not session
                or "\n" in session or "\x00" in session):
            raise AdapterBlocked("missing or malformed official owner markers")
        try:
            check_source(self.source_root, self.source_sha)
        except OwnerServiceError as exc:
            raise AdapterBlocked("pinned checkout cannot be attested") from exc
        wrapper = self.source_root / "scripts/with_pim_board.sh"
        if not wrapper.is_file() or wrapper.is_symlink():
            raise AdapterBlocked("reviewed board lease verifier is missing")
        argv = [
            "/bin/bash", str(wrapper), "--check-held",
            "--until", self.expected_until,
            "--long-lease", "true", "--purpose", self.purpose,
        ]
        try:
            result = self.runner(
                argv, shell=False, timeout=12, cwd=str(self.source_root),
                env=self.env, capture_output=True, text=True, check=False)
        except (OSError, subprocess.TimeoutExpired, TypeError) as exc:
            raise AdapterBlocked("official lease verification failed") from exc
        if result.returncode != 0:
            raise AdapterBlocked("official long lease not confirmed")
        return VerifiedLongLease(
            session=session, remaining_seconds=remaining,
            source_sha=self.source_sha)


@dataclass(frozen=True)
class BoardSample:
    """Raw sample returned by an independently audited read-only inspector."""
    target: str
    source_sha: str
    observed_at_epoch: int
    journal_state: str
    journal_phase: str | None
    journal_run_id: str | None
    journal_original_sha: str | None
    edgeconf_sha: str
    ord_conf_sha: str
    runtime_sha: str
    led_flags: tuple[bool, ...]
    binary_hashes: Mapping[str, str]
    consumers_ready: bool
    writer_closed: bool
    owner_idle: bool


class BoardInspector(Protocol):
    def observe(self) -> BoardSample: ...


class PinnedBoardRecoveryAdapter:
    """Narrow bridge to the EXISTING strict recovery (no generic command API).

    Does not implement the BoardInspector: the actual inspector is still a
    required missing integration, and cannot be fabricated from the presence
    of a JSON flag or an unlocked environment variable.
    """

    def __init__(
        self, *, baseline: Baseline, lease: OfficialLeaseVerifier,
        inspector: BoardInspector, setup_manager: object,
        installed_hashes: Mapping[str, str],
        stabilize_sec: int = 120,
    ) -> None:
        if (type(baseline) is not Baseline
                or not isinstance(lease, OfficialLeaseVerifier)
                or not hasattr(inspector, "observe")
                or not hasattr(setup_manager, "ssh")
                or not callable(getattr(setup_manager, "reboot_and_wait", None))):
            raise AdapterBlocked("incomplete approved owner restoration context")
        if (type(stabilize_sec) is not int or
                not 30 <= stabilize_sec <= 300):
            raise AdapterBlocked("invalid bounded restoration stabilization")
        manifest = dict(installed_hashes)
        if (set(manifest) != set(REQUIRED_BINARIES)
                or any(type(v) is not str or not _SHA256.fullmatch(v)
                       for v in manifest.values())):
            raise AdapterBlocked("incomplete installed binary hash manifest")
        if baseline.source_sha != lease.source_sha or baseline.target != TARGET:
            raise AdapterBlocked("pinned source/board target mismatch")
        self.baseline = baseline
        self.lease = lease
        self.inspector = inspector
        self.setup_manager = setup_manager
        self.installed_hashes = manifest
        self.stabilize_sec = stabilize_sec
        self._recovery_attempted = False

    def _verified_observation(self) -> tuple[BoardEvidence, BoardSample]:
        proof = self.lease.verify()
        sample = self.inspector.observe()
        if type(sample) is not BoardSample:
            raise AdapterBlocked("untrusted board observation type")
        age = int(self.lease.clock() - sample.observed_at_epoch)
        if (type(sample.observed_at_epoch) is not int
                or age < 0 or age > MAX_EVIDENCE_AGE_SECONDS
                or sample.target != TARGET
                or sample.source_sha != self.baseline.source_sha):
            raise AdapterBlocked("source, target or freshness mismatch")
        if (not isinstance(sample.binary_hashes, Mapping)
                or dict(sample.binary_hashes) != self.installed_hashes):
            raise AdapterBlocked("installed executable hashes do not match")
        for field in ("edgeconf_sha", "ord_conf_sha", "runtime_sha"):
            value = getattr(sample, field)
            if type(value) is not str or not _SHA256.fullmatch(value):
                raise AdapterBlocked("untrusted board file hash")
        if sample.ord_conf_sha != self.baseline.ord_conf_sha:
            raise AdapterBlocked("ORD/VCM baseline diverged")
        if sample.led_flags != (False, False, False, False):
            raise AdapterBlocked("all MCP4018-missing LED flags must remain OFF")
        for name in ("consumers_ready", "writer_closed", "owner_idle"):
            if type(getattr(sample, name)) is not bool:
                raise AdapterBlocked("invalid verified status flag")
        if sample.writer_closed is not True or sample.owner_idle is not True:
            raise AdapterBlocked("writer not quiesced or camera owner busy")
        if sample.journal_state == "ABSENT":
            if (sample.journal_phase is not None
                    or sample.journal_run_id is not None
                    or sample.journal_original_sha is not None):
                raise AdapterBlocked("ambiguous closed on-board journal")
        elif sample.journal_state == "DIRTY":
            if (sample.journal_run_id != self.baseline.run_id
                    or sample.journal_original_sha != self.baseline.edgeconf_sha
                    or sample.journal_phase not in {
                        "JOURNALED", "APPLIED", "REBOOTED",
                        "RESTORED", "VERIFIED", "CLOSED",
                    }):
                raise AdapterBlocked("untrusted on-board recovery manifest")
        else:
            raise AdapterBlocked("unknown on-board journal status")
        # Re-check active lease after potentially slow board inspection:
        # no evidence may be returned using an expired or replaced holder.
        renewed = self.lease.verify()
        if renewed.session != proof.session:
            raise AdapterBlocked("owner lease changed mid-inspection")
        return BoardEvidence(
            target=sample.target, source_sha=self.baseline.source_sha,
            source_attested=True, installed_binaries_attested=True,
            exclusive_lease_attested=True, lease_session=proof.session,
            lease_remaining_seconds=renewed.remaining_seconds,
            age_seconds=age, on_board_journal_state=sample.journal_state,
            on_board_run_id=sample.journal_run_id,
            on_board_original_sha=sample.journal_original_sha,
            edgeconf_sha=sample.edgeconf_sha,
            ord_conf_sha=sample.ord_conf_sha,
            runtime_sha=sample.runtime_sha,
            all_led_off=True, consumers_ready=sample.consumers_ready,
            writer_closed=sample.writer_closed,
            owner_idle=sample.owner_idle), sample

    def observe(self) -> BoardEvidence:
        return self._verified_observation()[0]

    def recover_validated_board_journal(
        self, *, run_id: str, original_sha256: str,
    ) -> None:
        if self._recovery_attempted:
            raise AdapterBlocked("repeat recovery requires independent reconciliation")
        if run_id != self.baseline.run_id or original_sha256 != self.baseline.edgeconf_sha:
            raise AdapterBlocked("foreign run or original hash")
        row, raw = self._verified_observation()
        phase = raw.journal_phase
        if (row.on_board_journal_state != "DIRTY"
                or row.on_board_run_id != run_id
                or row.on_board_original_sha != original_sha256
                or phase not in _ALLOWED_RECOVERY_PHASES):
            raise AdapterBlocked(
                "journal not proven applied; no blind reboot from JOURNALED "
                "or an already-restoring phase")
        # The existing recover_pending_transaction is the ONLY mutating call.
        # It validates the onboard manifest and original before restoring,
        # reboots for readiness, verifies SHA and closes the board journal.
        # Persistent host intent and owner lease must already be held by the
        # caller; this adapter is not an entry point or authorization.
        self._recovery_attempted = True
        result = recover_pending_transaction(
            self.setup_manager, conf_path=EDGECONF_PATH,
            stabilize_sec=self.stabilize_sec)
        if result is not True:
            raise AdapterBlocked("validated board recovery did not complete")
