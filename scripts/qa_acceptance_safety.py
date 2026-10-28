#!/usr/bin/env python3
"""Fail-closed read-only admission gate for qa_recovery_acceptance.

This file never invokes the mutating QA Plan, deletes SD data, reboots a target,
or acquires a board lease. Live checks require the existing official lease.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BASE_COMMIT = "e7c93b783d242e696cbc96fa12d04dec46e495c0"
ONLY_COMMITTED_PATHS = {"scripts/qa_acceptance_safety.py", "tests/test_qa_acceptance_safety.py"}
HOST = "192.168.214.4"
PLAN_SECONDS = 45 * 60
LONG_LEASE_SECONDS = 90 * 60
HOST_LIMIT_SECONDS = 1800
CLEANUP_SECONDS = 1800 + 60
ADMISSION_MARGIN_SECONDS = 60
RECORD_KBPS = 1024 + 2048 + 4096 + 8192
RECORD_CHANNELS = 4
MIN_FILE_RESERVE = 100
MIN_DISK_MARGIN_POINTS = 5.0
EVENT_FILE_LIMIT = 15000


class GateError(ValueError):
    pass


@dataclass(frozen=True)
class Snapshot:
    used_kib: int
    avail_kib: int
    root_files: int
    event_files: int
    event_kib: int
    recycle_kib: int
    disk_limit_per: int
    disk_limit_file: int
    event_storage_size: int
    disk_manage: bool
    event_auto_remove: bool
    recovery_files: int
    stale_restart_markers: bool


def _natural(value: object, name: str, positive: bool = False) -> int:
    if type(value) is not int or value < int(positive):
        raise GateError("INVALID_" + name.upper())
    return value


def parse_observation(data: dict[str, str]) -> Snapshot:
    """Validate a fixed-scope SSH observation; absent or malformed is BLOCK."""
    try:
        df = data["df"].split()
        if len(df) != 6 or df[-1] != "/mnt/sd_cam" or not df[4].endswith("%"):
            raise GateError("INVALID_DF")
        total, used, avail = (int(df[i]) for i in (1, 2, 3))
        if total <= 0 or used <= 0 or avail < 0 or used + avail > total:
            raise GateError("INVALID_FS_CAPACITY")
        policy = json.loads(data["ord"])
        camera = json.loads(data["camera"])
        def count(name: str) -> int:
            raw = data[name].strip()
            if re.fullmatch(r"[0-9]+", raw) is None:
                raise GateError("INVALID_" + name.upper())
            return int(raw)
        def size(name: str, suffix: str) -> int:
            pattern = r"([0-9]+)\s+/mnt/sd_cam/" + suffix
            match = re.fullmatch(pattern, data[name].strip())
            if match is None:
                raise GateError("INVALID_" + name.upper())
            return int(match.group(1))
        disk_limit = _natural(policy["disk_limit_per"], "disk_limit", True)
        file_limit = _natural(policy["disk_limit_file"], "file_limit", True)
        event_quota = _natural(camera["event_storage_size"], "event_quota", True)
        if disk_limit > 100 or event_quota > 100:
            raise GateError("INVALID_QUOTA")
        disk_manage, auto_remove = policy["disk_manage"], camera["event_auto_remove"]
        if type(disk_manage) is not bool or type(auto_remove) is not bool:
            raise GateError("INVALID_POLICY_FLAGS")
        markers = data["markers"]
        if markers not in {"PRESENT", "ABSENT"}:
            raise GateError("INVALID_STALE_MARKERS")
        return Snapshot(
            used, avail, count("root_files"), count("event_files"),
            size("event_kib", "event"), size("recycle_kib", "recycle"),
            disk_limit, file_limit, event_quota, disk_manage, auto_remove,
            count("recovery_files"), markers == "PRESENT",
        )
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        if isinstance(exc, GateError):
            raise
        raise GateError("INVALID_OBSERVATION") from None


def storage_gate(s: Snapshot) -> dict[str, object]:
    for key in ("used_kib", "avail_kib", "root_files", "event_files",
                "event_kib", "recycle_kib", "recovery_files"):
        _natural(getattr(s, key), key)
    for key in ("disk_limit_per", "disk_limit_file", "event_storage_size"):
        _natural(getattr(s, key), key, True)
    if s.disk_limit_per > 100 or s.event_storage_size > 100 or not s.used_kib + s.avail_kib:
        raise GateError("INVALID_POLICY")
    if any(type(v) is not bool for v in (s.disk_manage, s.event_auto_remove, s.stale_restart_markers)):
        raise GateError("INVALID_POLICY_FLAGS")
    capacity = s.used_kib + s.avail_kib
    pct = s.used_kib * 100 / capacity
    slack = max(0, int(capacity * s.disk_limit_per / 100 - s.used_kib))
    projected_kib = int(RECORD_KBPS * 1000 / 8 * PLAN_SECONDS / 1024)
    projected_files = RECORD_CHANNELS * ((PLAN_SECONDS + 59) // 60)
    event_quota_kib = capacity * s.event_storage_size / 100
    reasons = []
    if not s.disk_manage:
        reasons.append("DISK_MANAGEMENT_DISABLED")
    if pct > s.disk_limit_per - MIN_DISK_MARGIN_POINTS:
        reasons.append("SD_CAPACITY_MARGIN_INSUFFICIENT")
    if projected_kib >= slack:
        reasons.append("PLANNED_RECORDING_CROSSES_DISK_LIMIT")
    if s.root_files + MIN_FILE_RESERVE >= s.disk_limit_file:
        reasons.append("ROOT_FILE_COUNT_CLOSE_TO_LIMIT")
    if s.root_files + projected_files >= s.disk_limit_file:
        reasons.append("PLANNED_RECORDING_CROSSES_FILE_LIMIT")
    if s.event_files + MIN_FILE_RESERVE >= EVENT_FILE_LIMIT:
        reasons.append("EVENT_FILE_COUNT_CLOSE_TO_LIMIT")
    if s.event_kib >= event_quota_kib:
        reasons.append("EVENT_STORAGE_LIMIT_REACHED")
    if s.recovery_files:
        reasons.append("DIRTY_RECOVERY_JOURNAL")
    if s.stale_restart_markers:
        reasons.append("STALE_PROCESS_RESTART_MARKERS")
    return {
        "allowed": not reasons, "reason_codes": reasons,
        "effective_used_pct": round(pct, 2), "limit_pct": s.disk_limit_per,
        "headroom_to_limit_kib": slack, "projected_recording_kib": projected_kib,
        "root_files": s.root_files, "root_file_limit": s.disk_limit_file,
        "projected_root_files": projected_files,
        "recycle_kib": s.recycle_kib, "event_files": s.event_files,
        "event_kib": s.event_kib, "event_quota_kib": int(event_quota_kib),
    }


def execution_budget_gate(
    host_limit: int = HOST_LIMIT_SECONDS,
    lease_seconds: int = LONG_LEASE_SECONDS,
    expected_seconds: int = PLAN_SECONDS,
) -> dict[str, object]:
    for k, v in (("host_limit", host_limit), ("lease", lease_seconds), ("expected", expected_seconds)):
        _natural(v, k, True)
    window = max(0, lease_seconds - CLEANUP_SECONDS)
    reasons = []
    if window < expected_seconds + ADMISSION_MARGIN_SECONDS:
        reasons.append("LEASE_EXECUTION_WINDOW_TOO_SHORT")
    if host_limit < lease_seconds + ADMISSION_MARGIN_SECONDS:
        reasons.append("HOST_TIMEOUT_CAN_INTERRUPT_RECOVERY")
    return {
        "allowed": not reasons, "reason_codes": reasons,
        "host_limit_seconds": host_limit, "lease_seconds": lease_seconds,
        "expected_seconds": expected_seconds,
        "cleanup_reserved_seconds": CLEANUP_SECONDS,
        "execution_window_seconds": window,
    }


def lease_is_valid(data: object, env: dict[str, str]) -> bool:
    try:
        if (env.get("PIM_BOARD_LOCK_HELD") != "1"
                or env.get("PIM_BOARD_LOCK_BOARD_ID") != "pim"
                or not env["PIM_BOARD_LOCK_OWNER_PID"].isdecimal()
                or not env["PIM_BOARD_LOCK_SESSION"]):
            return False
        boards = [b for b in data["result"]["boards"] if b.get("board_id") == "pim"]
        holders = [h for b in boards for h in b["holders"]]
        return (
            len(boards) == 1 and len(holders) == 1
            and holders[0].get("session") == env["PIM_BOARD_LOCK_SESSION"]
            and holders[0].get("mode") == "exclusive"
            and holders[0].get("liveness") == "alive"
            and holders[0].get("expired") is False
        )
    except (TypeError, KeyError, ValueError):
        return False


# Every SSH command is static, read-only and contains no user input.
PROBES = {
    "df": "df -kP /mnt/sd_cam | tail -n 1",
    "ord": "jq -c '{disk_limit_per:.ORD.disk_limit_per,disk_limit_file:.ORD.disk_limit_file,disk_manage:.ORD.disk_manage}' /root/shared_v/ord_vcm_conf.json",
    "camera": "jq -c '{event_storage_size:.VHL_CAM.event_storage_size,event_auto_remove:.VHL_CAM.event_auto_remove}' /root/shared_v/edgeconf_pim.json",
    "root_files": "find /mnt/sd_cam -mindepth 1 -maxdepth 1 -type f | wc -l",
    "event_files": "find /mnt/sd_cam/event -mindepth 1 -maxdepth 1 -type f | wc -l",
    "event_kib": "timeout 20 du -sk /mnt/sd_cam/event 2>/dev/null",
    "recycle_kib": "timeout 20 du -sk /mnt/sd_cam/recycle 2>/dev/null",
    "recovery_files": "if [ -d /root/shared_v/pim-check-recovery ]; then find /root/shared_v/pim-check-recovery -maxdepth 2 -type f | wc -l; else echo 0; fi",
    "markers": "if [ -e /tmp/pim_restart_smoke_before ] || [ -e /tmp/pim_restart_smoke_state ]; then echo PRESENT; else echo ABSENT; fi",
}


def read_live_board() -> Snapshot:
    if os.environ.get("PIM_BOARD_LOCK_HELD") != "1":
        raise GateError("EXCLUSIVE_LEASE_REQUIRED")
    status = subprocess.run(
        ["/home/jhw/.nvm/versions/node/v24.12.0/bin/node",
         "/home/jhw/.local/bin/jhw-control", "board", "status", "pim"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if status.returncode:
        raise GateError("CENTRAL_LEASE_STATUS_UNAVAILABLE")
    try:
        payload = json.loads(status.stdout)
    except ValueError:
        raise GateError("CENTRAL_LEASE_STATUS_INVALID") from None
    if not lease_is_valid(payload, os.environ):
        raise GateError("EXCLUSIVE_LEASE_NOT_VERIFIED")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from user_config import load_user_config
    from ssh import SshClient
    defaults = load_user_config()
    raw = {}
    with SshClient("192.168.214.4", user=defaults.get("default_user") or "root",
                   password=defaults.get("default_password") or "root",
                   connect_timeout=12, command_timeout=40) as client:
        for name, command in PROBES.items():
            value = client.run(command)
            if not isinstance(value, str):
                raise GateError("BOARD_PROBE_UNAVAILABLE")
            raw[name] = value
    return parse_observation(raw)


def source_is_pinned(root: Path) -> bool:
    """Admit only a clean, two-file safety commit atop verified QA source."""
    def git(*args: str) -> str:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=12, check=True,
        ).stdout.strip()
    return (
        git("rev-parse", "HEAD^") == BASE_COMMIT
        and set(git("diff", "--name-only", "HEAD^", "HEAD", "--").splitlines()) == ONLY_COMMITTED_PATHS
        and not git("status", "--porcelain=v1", "--untracked-files=all")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Requires real canonical PIM board lease; never mutates")
    args = parser.parse_args(argv)
    result = {"schema": 1, "mode": "live" if args.live else "offline",
              "plan": "qa_recovery_acceptance", "budget": execution_budget_gate()}
    try:
        if args.live:
            wt = Path(__file__).resolve().parents[1]
            if not source_is_pinned(wt):
                raise GateError("UNREVIEWED_SOURCE_TREE")
            result["storage"] = storage_gate(read_live_board())
        result["decision"] = ("ALLOW_PREPARATION" if result["budget"]["allowed"]
                              and (not args.live or result["storage"]["allowed"]) else "BLOCK")
    except (GateError, OSError, subprocess.SubprocessError):
        result["decision"] = "BLOCK"
        result["reason_codes"] = ["PREFLIGHT_UNAVAILABLE_OR_UNTRUSTED"]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"] == "ALLOW_PREPARATION" else 3


if __name__ == "__main__":
    raise SystemExit(main())
