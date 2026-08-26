from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

from hw_gate.rules import EvidenceError


RAW_TAIL_BYTES = 16_384
DMESG_LINES = 200
_PROCESS_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_EDGECONF_COMMAND = (
    "jq -c '{encoder:.VHL_CAM.enc,ch0:{bps:.VHL_CAM.i2c2.ch0.bps,"
    "qp_min:.VHL_CAM.i2c2.ch0.qp_min,qp_max:.VHL_CAM.i2c2.ch0.qp_max,"
    "quant:.VHL_CAM.i2c2.ch0.quant,profile:.VHL_CAM.i2c2.ch0.profile}}' "
    "/root/shared_v/edgeconf_pim.json"
)
_MODULE_COMMAND = (
    "set -u; module_path=$(modinfo -n max9296); "
    "case \"$module_path\" in /lib/modules/*) sha256sum -- \"$module_path\";; *) exit 64;; esac; "
    "modinfo -F version max9296; modinfo -F srcversion max9296; "
    "modprobe --dump-modversions \"$module_path\" | head -n 50"
)


def _bounded_text(value: object, limit: int = RAW_TAIL_BYTES) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else b""
    return raw[-limit:].decode("utf-8", errors="ignore")


def _read_tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - RAW_TAIL_BYTES))
            return stream.read(RAW_TAIL_BYTES).decode("utf-8", errors="ignore")
    except OSError as exc:
        return "diagnostic read failed: {0}".format(exc)


def _run(ssh: object, command: str) -> str:
    try:
        output = ssh.run(command)  # type: ignore[attr-defined]
    except Exception as exc:  # diagnostics must not hide the primary outcome
        return "diagnostic command failed: {0}".format(exc)
    return _bounded_text(output)


def collect_diagnostics(
    ssh: object,
    raw_dir: Path,
    process_names: Iterable[str],
) -> List[dict]:
    """Collect only bounded, allowlisted, read-only failure context."""
    names = tuple(process_names)
    if not names or any(not isinstance(name, str) or not _PROCESS_RE.fullmatch(name) for name in names):
        raise EvidenceError("diagnostic process name is outside the allowlist")

    dmesg_output = _run(ssh, "dmesg --color=never | tail -n 200")
    dmesg_output = "\n".join(dmesg_output.splitlines()[-DMESG_LINES:])
    selected_config = _run(ssh, _EDGECONF_COMMAND)
    # Names are already restricted to a non-shell metacharacter alphabet.
    # Only regex-significant dots need quoting inside this fixed ERE.
    process_pattern = "|".join(name.replace(".", r"\.") for name in names)
    process_command = (
        "ps -eo pid=,comm=,stat= | grep -E "
        "'^[[:space:]]*[0-9]+[[:space:]]+({0})[[:space:]]' | head -n 50"
    ).format(process_pattern)
    processes = _run(ssh, process_command)
    module = _run(ssh, _MODULE_COMMAND)

    diagnostics: List[dict] = [
        {"id": "dmesg", "output": dmesg_output},
        {"id": "edgeconf.selected", "output": selected_config},
        {"id": "processes.declared", "output": processes},
        {"id": "module.max9296", "output": module},
    ]
    if raw_dir.is_dir():
        for path in sorted(raw_dir.iterdir(), key=lambda item: item.name):
            if path.is_file() and not path.is_symlink():
                diagnostics.append({"id": "raw:{0}".format(path.name), "output": _read_tail(path)})
    return diagnostics
