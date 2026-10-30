"""Bounded kernel error evidence; pure diagnostics, no I/O or verdict override.

An observation boot ID and wall-clock range do not prove each source line's
boot identity. Temporal/device groups are candidates, never root causes.
"""
from __future__ import annotations

from datetime import datetime
import json
import re

MAX_SAMPLES = 32
MAX_RECORD_BYTES = 512
MAX_PAYLOAD_BYTES = 20000
GROUP_WINDOW_SECONDS = 2.0
SOURCE = "/var/log/cantops/kern.log"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_HEADER = re.compile(
    r"COUNT=([1-9][0-9]{0,11}) SCOPE=(SESSION|BOOT_FALLBACK) "
    r"START=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"BOOT_ID=(" + _UUID + r") FIRST=.{0,180} LAST=.{0,180}"
)
_FOOTER = re.compile(r"SAMPLES=([0-9]{1,2}) OMITTED=([0-9]{1,12}) CLIPPED=([0-9]{1,2})")
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.[0-9]{1,6})?)(?=\s)")
_BUS = re.compile(r"\[I2C:([0-9]+)\]")
_CHANNEL = re.compile(r"\bch([0-9]+)\b")


def _base() -> dict:
    return {
        "schema": 1, "source": SOURCE, "status": "invalid", "count": None,
        "scope": None, "start": None, "boot_id": None,
        "boot_id_provenance": "collector_current_boot",
        "per_line_boot_identity_verified": False,
        "records": [], "candidate_groups": [], "grouping_is_causal": False,
        "grouping_complete": False, "sample_duplicate_count": 0,
        "omitted_count": None, "clipped_count": None,
        "recovery_status": "UNVERIFIED", "root_cause": "UNKNOWN",
    }


def _kind(raw: str) -> str:
    if "MCP4018" in raw and "write fail" in raw:
        return "mcp4018_write_failure"
    if "applied fail" in raw:
        return "channel_apply_failure"
    return "unclassified"


def _groups(records: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for index, record in enumerate(records):
        stamp = datetime.fromisoformat(record["timestamp"])
        key = (record["adapter"], record["channel"])
        group = None
        if None not in key:
            for candidate in reversed(groups):
                if (candidate["adapter"], candidate["channel"]) != key:
                    continue
                first = datetime.fromisoformat(candidate["first_timestamp"])
                last = datetime.fromisoformat(candidate["last_timestamp"])
                if last <= stamp and 0 <= (stamp - first).total_seconds() <= GROUP_WINDOW_SECONDS:
                    group = candidate
                break
        if group is None:
            group = {
                "adapter": key[0], "channel": key[1],
                "first_timestamp": record["timestamp"], "last_timestamp": record["timestamp"],
                "record_indexes": [], "kinds": [],
                "basis": "temporal_device_candidate" if None not in key else "unattributed_single_line",
                "recovery_status": "UNVERIFIED",
            }
            groups.append(group)
        group["record_indexes"].append(index)
        group["last_timestamp"] = record["timestamp"]
        if record["kind"] not in group["kinds"]:
            group["kinds"].append(record["kind"])
    return groups


def parse_kernel_evidence(output: object) -> dict:
    """Parse only the versioned, bounded collector response; never infer success.

    Exact zero preserves the legacy no-matches result, without claiming that
    recovery occurred or hardware is healthy. Every other malformed input is
    invalid evidence. The caller still applies its original expected-zero gate.
    """
    evidence = _base()
    if not isinstance(output, str):
        evidence["error"] = "MISSING_OR_OVERSIZED_EVIDENCE"
        return evidence
    try:
        encoded_size = len(output.encode("utf-8"))
    except UnicodeEncodeError:
        evidence["error"] = "INVALID_TEXT_ENCODING"
        return evidence
    if encoded_size > MAX_PAYLOAD_BYTES:
        evidence["error"] = "MISSING_OR_OVERSIZED_EVIDENCE"
        return evidence
    if output.strip() in {
        "NO_SOURCE", "READ_ERROR", "INVALID_START_TIME", "INVALID_BOOT_ID",
        "BOOT_CHANGED", "INVALID_LOG_TIME",
    }:
        evidence["error"] = output.strip()
        return evidence
    if output.strip() == "0":
        evidence.update(status="no_matches", count=0, recovery_status="NOT_ASSESSED",
                        omitted_count=0, clipped_count=0)
        return evidence
    lines = output.rstrip("\n").split("\n")
    try:
        if len(lines) < 3 or len(lines) > MAX_SAMPLES + 2:
            raise ValueError("INVALID_ENVELOPE")
        header = _HEADER.fullmatch(lines[0])
        footer = _FOOTER.fullmatch(lines[-1])
        if header is None or footer is None:
            raise ValueError("INVALID_ENVELOPE")
        count = int(header[1])
        start = datetime.strptime(header[3], "%Y-%m-%d %H:%M:%S")
        sample_count, omitted, clipped = map(int, footer.groups())
        if (sample_count != min(count, MAX_SAMPLES) or len(lines) != sample_count + 2
                or count != sample_count + omitted or not 0 <= clipped <= sample_count):
            raise ValueError("INCONSISTENT_COUNTS")
        records = []
        for line in lines[1:-1]:
            if not line.startswith("RECORD="):
                raise ValueError("INVALID_RECORD")
            raw = line[len("RECORD="):]
            if len(raw.encode("utf-8")) > MAX_RECORD_BYTES:
                raise ValueError("OVERSIZED_RECORD")
            match = _STAMP.match(raw)
            if match is None:
                raise ValueError("INVALID_RECORD_TIME")
            stamp = datetime.fromisoformat(match[1])
            # The producer deliberately retains the existing second-granularity
            # scope boundary. Do not silently broaden or relax it in diagnostics.
            if stamp.replace(microsecond=0) <= start:
                raise ValueError("RECORD_OUTSIDE_SCOPE")
            buses, channels = _BUS.findall(raw), _CHANNEL.findall(raw)
            records.append({
                "raw": raw, "timestamp": match[1],
                "adapter": int(buses[0]) if len(buses) == 1 else None,
                "channel": int(channels[0]) if len(channels) == 1 else None,
                "kind": _kind(raw),
            })
        evidence.update(
            status="partial" if omitted or clipped else "complete", count=count,
            scope=header[2], start=header[3], boot_id=header[4], records=records,
            candidate_groups=_groups(records), grouping_complete=not (omitted or clipped),
            sample_duplicate_count=sample_count - len({r["raw"] for r in records}),
            omitted_count=omitted, clipped_count=clipped,
        )
    except (ValueError, OverflowError):
        evidence = _base()
        evidence["error"] = "MALFORMED_KERNEL_EVIDENCE"
    return evidence


def format_kernel_evidence(evidence: dict) -> str:
    """Bounded one-line presentation; escape kernel-controlled terminal text."""
    if evidence.get("status") == "invalid":
        return "KERNEL_EVIDENCE_INVALID: " + evidence.get("error", "UNKNOWN")
    if evidence.get("status") == "no_matches":
        return "0"
    records = evidence["records"]
    first = json.dumps(records[0]["raw"][:180], ensure_ascii=True)
    last = json.dumps(records[-1]["raw"][:180], ensure_ascii=True)
    return (
        f"COUNT={evidence['count']} SCOPE={evidence['scope']} START={evidence['start']} "
        f"BOOT_ID={evidence['boot_id']} CANDIDATE_GROUPS={len(evidence['candidate_groups'])} "
        f"SAMPLE_DUPLICATES={evidence['sample_duplicate_count']} SAMPLES={len(records)} OMITTED={evidence['omitted_count']} CLIPPED={evidence['clipped_count']} "
        f"RECOVERY=UNVERIFIED FIRST={first} LAST={last}"
    )
