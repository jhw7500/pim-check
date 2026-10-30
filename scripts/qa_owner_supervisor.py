"""Fail-closed restartable owner daemon for a future PIM QA campaign.

This service owns durable local observation and refuses every hardware action.
It does not falsely treat a local journal or running watchdog as a real board
restoration. The actual board adapter requires separate admission and audit.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Iterator

from scripts.qa_durable_journal import JournalError, JournalStore

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class OwnerServiceError(RuntimeError):
    """Missing trust proof, inaccessible state, or conflicting owner."""


def check_source(root: Path, source_sha: str) -> None:
    if type(source_sha) is not str or not SHA_RE.fullmatch(source_sha):
        raise OwnerServiceError("invalid pinned source SHA")
    if root.is_symlink() or not root.is_dir():
        raise OwnerServiceError("untrusted source path")
    for command, expected in (("rev-parse", source_sha), ("status", "")):
        args = (["git", "-C", str(root), "rev-parse", "HEAD"]
                if command == "rev-parse" else
                ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"])
        try:
            result = subprocess.run(args, check=False, text=True, capture_output=True, timeout=8)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OwnerServiceError("source cannot be attested") from exc
        if result.returncode or result.stdout.strip() != expected:
            raise OwnerServiceError("deployed checkout does not match source pin")


def open_state_dir(path: Path) -> int:
    if path.is_symlink():
        raise OwnerServiceError("state directory is a symlink")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.getuid()):
            os.close(fd)
            raise OwnerServiceError("state directory must be owned 0700")
        return fd
    except OSError as exc:
        raise OwnerServiceError("owner state unavailable") from exc


@contextmanager
def owner_lock(path: Path) -> Iterator[None]:
    dfd = open_state_dir(path)
    fd = None
    try:
        fd = os.open(".owner-service.lock", os.O_CREAT | os.O_RDWR
                     | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        info = os.fstat(fd)
        name = os.stat(".owner-service.lock", dir_fd=dfd, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
                or (info.st_ino, info.st_dev) != (name.st_ino, name.st_dev)):
            raise OwnerServiceError("unsafe owner lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OwnerServiceError("owner already running") from exc
        os.fsync(dfd)
        yield
    except OSError as exc:
        raise OwnerServiceError("owner lock unavailable") from exc
    finally:
        if fd is not None:
            os.close(fd)
        os.close(dfd)


def store_status(state_dir: Path, result: dict) -> None:
    dfd = open_state_dir(state_dir)
    fd = None
    tmp = ".owner-status-" + secrets.token_hex(10)
    created = False
    try:
        try:
            old = os.stat("owner-status.json", dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            old = None
        if old and (not stat.S_ISREG(old.st_mode) or old.st_nlink != 1
                    or old.st_uid != os.getuid() or stat.S_IMODE(old.st_mode) != 0o600):
            raise OwnerServiceError("unsafe owner status")
        data = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                     | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        created = True
        remaining = memoryview(data)
        while remaining:
            n = os.write(fd, remaining)
            if n <= 0:
                raise OSError("short status write")
            remaining = remaining[n:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp, "owner-status.json", src_dir_fd=dfd, dst_dir_fd=dfd)
        created = False
        os.fsync(dfd)
    except OSError as exc:
        raise OwnerServiceError("status persistence failed") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if created:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        os.close(dfd)


def sd_notify(data: str) -> None:
    name = os.environ.get("NOTIFY_SOCKET")
    if name:
        address = ("\0" + name[1:]) if name.startswith("@") else name
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.sendto(data.encode(), address)
        except OSError:
            pass


class OwnerSupervisor:
    """Persistent incident classifier; never a target-mutating controller."""

    def __init__(self, state_dir: Path, source_root: Path, source_sha: str):
        self.state_dir = Path(state_dir)
        self.source_root = Path(source_root)
        self.source_sha = source_sha
        if type(source_sha) is not str or not SHA_RE.fullmatch(source_sha):
            raise OwnerServiceError("invalid source SHA")
        fd = open_state_dir(self.state_dir)
        os.close(fd)

    def inspect(self) -> dict:
        code = "NO_CAMPAIGN_REGISTERED"
        revision = None
        source_verified = True
        try:
            check_source(self.source_root, self.source_sha)
        except OwnerServiceError:
            source_verified = False
        try:
            journal = JournalStore(self.state_dir)
            record = journal.read(expected_source_sha=self.source_sha)
            if record["state"] == "MUTATION_PENDING" and source_verified:
                record = journal.transition(
                    "RESTORE_PENDING", expected_revision=record["revision"])
            revision = record["revision"]
            code = {
                "PREPARED": "NO_MUTATION_CONFIRMED",
                "BASELINE_SAVED": "NO_MUTATION_CONFIRMED",
                "RESTORE_PENDING": "TARGET_RESTORE_REQUIRED",
                "FINAL_ATTESTATION_PENDING": "FINAL_ATTESTATION_REQUIRED",
            }.get(record["state"], "RECOVERY_STATE_UNKNOWN")
        except JournalError as exc:
            if "journal missing" not in str(exc):
                code = "RECOVERY_JOURNAL_UNTRUSTED"
        if not source_verified:
            code = "DEPLOYED_SOURCE_UNVERIFIED"
        return {
            "schema": 1, "source_sha": self.source_sha, "target": "192.168.214.4",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "reason_code": code, "journal_revision": revision,
            "decision": "BLOCK", "execution_authorized": False,
            "board_touched": False, "real_restore_performed": False,
        }

    def tick(self) -> dict:
        status = self.inspect()
        store_status(self.state_dir, status)
        return status

    def serve(self, *, once: bool = False, interval: float = 5) -> dict:
        if not 0 < interval <= 60:
            raise OwnerServiceError("invalid interval")
        stopping = False

        def stop(_sig: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        with owner_lock(self.state_dir):
            if once:
                return self.tick()
            previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
            for s in previous:
                signal.signal(s, stop)
            try:
                status = self.tick()
                sd_notify("READY=1\nSTATUS=Owner observer ready; live board blocked")
                while not stopping:
                    sd_notify("WATCHDOG=1")
                    deadline = time.monotonic() + interval
                    while not stopping and time.monotonic() < deadline:
                        time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))
                    if not stopping:
                        status = self.tick()
                sd_notify("STOPPING=1")
                return status
            finally:
                for s, previous_handler in previous.items():
                    signal.signal(s, previous_handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PIM owner recovery observation service")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = OwnerSupervisor(args.state_dir, args.source_root, args.source_sha).serve(
            once=args.once, interval=args.interval)
        if args.once:
            print(json.dumps(result, sort_keys=True))
        return 0
    except (OwnerServiceError, JournalError) as exc:
        print("PIM owner observer blocked: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
