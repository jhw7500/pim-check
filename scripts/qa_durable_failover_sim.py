"""Offline-only owner recovery failover simulator, NOT a PIM QA runner.

A local simulated device models the durable intent journal's recovery
semantics after controller death. Every filesystem write is restricted
to a fixed-name simulated-device directory under the OS temp directory.

No SSH, PIM board command, real supervisor registration, SD cleanup,
system service, camera recorder, or execution authorization exists here.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

from scripts.qa_durable_journal import JournalStore

SIMULATED_FAULT = b"SIMULATED_FAULT_ONLY\n"
FIXTURE_DIRNAME = "simulated-device"
MAX_FIXTURE_BYTES = 4096


class SimulationError(RuntimeError):
    """Unresolved local simulation state; never assert restoration success."""


class SimulatedDevice:
    """A deliberately local fixture, not a general device/SSH adapter."""

    def __init__(self, directory: Path):
        root = Path(directory)
        temp_root = Path(tempfile.gettempdir()).resolve()
        # The exact name and temporary root rule prevent accidentally passing
        # /mnt/sd_cam or a project checkout as a simulated target.
        if (root.name != FIXTURE_DIRNAME or root.is_symlink()
                or not root.parent.resolve().is_relative_to(temp_root)):
            raise ValueError("offline-only simulated-device temp directory required")
        self.directory = root

    def initialize(self, baseline: bytes) -> None:
        if type(baseline) is not bytes or not 0 < len(baseline) <= MAX_FIXTURE_BYTES:
            raise ValueError("invalid simulated baseline")
        if self.directory.exists() or self.directory.is_symlink():
            raise SimulationError("simulation already initialized")
        self.directory.mkdir(mode=0o700)
        self._write("baseline.bin", baseline, create_only=True)
        self._write("current.bin", baseline, create_only=True)

    def _open_directory(self) -> int:
        try:
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY
                         | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise SimulationError("simulation storage unavailable") from exc
        dstat = os.fstat(fd)
        if (not stat.S_ISDIR(dstat.st_mode)
                or stat.S_IMODE(dstat.st_mode) != 0o700):
            os.close(fd)
            raise SimulationError("untrusted simulation directory")
        return fd

    def _read(self, name: str) -> bytes:
        dfd = self._open_directory()
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=dfd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or not 0 < info.st_size <= MAX_FIXTURE_BYTES):
                raise SimulationError("untrusted simulated fixture")
            content = bytearray()
            while len(content) <= MAX_FIXTURE_BYTES:
                block = os.read(fd, 1024)
                if not block:
                    return bytes(content)
                content.extend(block)
            raise SimulationError("simulated fixture too large")
        except OSError as exc:
            raise SimulationError("simulation read unavailable") from exc
        finally:
            if fd is not None:
                os.close(fd)
            os.close(dfd)

    def _write(self, name: str, data: bytes, *, create_only: bool = False) -> None:
        if name not in ("baseline.bin", "current.bin"):
            raise ValueError("unexpected simulated fixture name")
        if type(data) is not bytes or not 0 < len(data) <= MAX_FIXTURE_BYTES:
            raise ValueError("invalid simulation content")
        if not create_only:
            # A missing, symlinked, hardlinked or world-readable current file
            # indicates an untrusted local fixture. Do not quietly replace it
            # and misreport a verified recovery.
            self._read(name)
        dfd = self._open_directory()
        temp_name = name if create_only else (".simulation-" + secrets.token_hex(12) + ".tmp")
        fd = None
        created = False
        try:
            fd = os.open(temp_name, os.O_EXCL | os.O_CREAT | os.O_WRONLY
                         | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
            created = True
            remaining = memoryview(data)
            while remaining:
                n = os.write(fd, remaining)
                if n <= 0:
                    raise OSError("short simulated fixture write")
                remaining = remaining[n:]
            os.fsync(fd)
            os.close(fd)
            fd = None
            if not create_only:
                os.replace(temp_name, name, src_dir_fd=dfd, dst_dir_fd=dfd)
                created = False
            os.fsync(dfd)
        except OSError as exc:
            raise SimulationError("simulation persistence failed") from exc
        finally:
            if fd is not None:
                os.close(fd)
            if created and not create_only:
                try:
                    os.unlink(temp_name, dir_fd=dfd)
                except OSError:
                    pass
            os.close(dfd)

    def read_baseline(self) -> bytes:
        return self._read("baseline.bin")

    def read_current(self) -> bytes:
        return self._read("current.bin")

    def baseline_sha256(self) -> str:
        return hashlib.sha256(self.read_baseline()).hexdigest()

    def matches_baseline(self) -> bool:
        return self.read_current() == self.read_baseline()

    def simulate_fault(self) -> None:
        """Only modify the temporary fixture; never a real device."""
        self._write("current.bin", SIMULATED_FAULT)

    def restore(self) -> None:
        """Idempotently restore only the temporary fixture."""
        self._write("current.bin", self.read_baseline())


class OfflineCoordinator:
    """Local-only state-machine replay. Does not implement a live runner."""

    def __init__(self, journal_dir: Path, device_dir: Path, source_sha: str):
        if type(source_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
            raise ValueError("exact simulator source SHA required")
        self.journal = JournalStore(Path(journal_dir))
        self.device = SimulatedDevice(Path(device_dir))
        self.source_sha = source_sha

    @contextmanager
    def _owner_guard(self):
        """Serialize cooperating local owners across durable intent and its effect.

        The lock is held through simulated mutation or restore. SIGKILL
        releases the OS lock so the next process can inspect the journal.
        This is not real PIM board lease fencing.
        """
        dfd = self.device._open_directory()
        fd = None
        lock_ready = False
        try:
            fd = os.open(
                ".offline-owner.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=dfd,
            )
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = os.fstat(fd)
            named = os.stat(".offline-owner.lock", dir_fd=dfd, follow_symlinks=False)
            if (
                not stat.S_ISREG(locked.st_mode)
                or locked.st_nlink != 1
                or stat.S_IMODE(locked.st_mode) != 0o600
                or (locked.st_dev, locked.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise SimulationError("unsafe offline owner lock")
            os.fsync(dfd)
            lock_ready = True
            yield
        except OSError as exc:
            if lock_ready:
                raise
            raise SimulationError("offline owner lock unavailable") from exc
        finally:
            if fd is not None:
                os.close(fd)
            os.close(dfd)

    def _checked_record(self) -> dict:
        record = self.journal.read(expected_source_sha=self.source_sha)
        if self.device.baseline_sha256() != record["baseline_sha256"]:
            raise SimulationError("simulated baseline SHA mismatch")
        return record

    def prepare(self, *, run_id: str) -> dict:
        with self._owner_guard():
            return self._prepare_guarded(run_id=run_id)

    def _prepare_guarded(self, *, run_id: str) -> dict:
        if not self.device.matches_baseline():
            raise SimulationError("simulated device not at original baseline")
        return self.journal.begin(
            run_id=run_id, source_sha=self.source_sha,
            baseline_sha256=self.device.baseline_sha256())

    def simulate_mutation(self) -> dict:
        with self._owner_guard():
            return self._simulate_mutation_guarded()

    def _simulate_mutation_guarded(self) -> dict:
        record = self._checked_record()
        if record["state"] == "PREPARED":
            if not self.device.matches_baseline():
                raise SimulationError("unexpected simulated device change before intent")
            record = self.journal.transition(
                "BASELINE_SAVED", expected_revision=record["revision"])
        if record["state"] != "BASELINE_SAVED":
            raise SimulationError("cannot start simulated mutation from unresolved state")
        if not self.device.matches_baseline():
            raise SimulationError("unexpected simulated device change before intent")
        # Persist intent before the ONLY simulated side effect.
        record = self.journal.transition(
            "MUTATION_PENDING", expected_revision=record["revision"])
        self.device.simulate_fault()
        return record

    def recover(self) -> dict:
        with self._owner_guard():
            return self._recover_guarded()

    def _recover_guarded(self) -> dict:
        record = self._checked_record()
        state = record["state"]
        if state in ("PREPARED", "BASELINE_SAVED"):
            if not self.device.matches_baseline():
                raise SimulationError("unexpected mutation without durable intent")
            return record

        if state == "MUTATION_PENDING":
            # Persist restore intent *before* restoring. A crash between this
            # transition and verify will safely retry restore on restart.
            record = self.journal.transition(
                "RESTORE_PENDING", expected_revision=record["revision"])
            state = "RESTORE_PENDING"

        if state == "RESTORE_PENDING":
            try:
                self.device.restore()
                if not self.device.matches_baseline():
                    raise SimulationError("simulated restore verification failed")
            except (SimulationError, OSError) as exc:
                raise SimulationError("simulated restore failed; journal remains pending") from exc
            return self.journal.transition(
                "FINAL_ATTESTATION_PENDING", expected_revision=record["revision"])

        if state == "FINAL_ATTESTATION_PENDING":
            if not self.device.matches_baseline():
                raise SimulationError("simulated final baseline mismatch")
            return record

        raise SimulationError("unexpected journal state; no simulation")
