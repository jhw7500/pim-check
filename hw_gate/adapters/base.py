from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ssh import SshClient


@dataclass(frozen=True)
class AdapterContext:
    ssh: SshClient
    baseline_gate: dict
    run_id: str
    raw_dir: Path


class HardwareGateAdapter(Protocol):
    adapter_id: str
    schema_version: int

    def run(self, context: AdapterContext) -> dict:
        """Return canonical gate evidence; never infer evidence from an exit code."""
