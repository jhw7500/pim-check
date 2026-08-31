from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from checks.target_identity import TargetIdentityCheck
from hw_gate.rules import EvidenceError
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


def verify_target_identity(ssh: SshClient, baseline: dict) -> None:
    """Fail closed unless the connected target matches every baseline identity claim."""
    config = {"target_identity": baseline.get("target_identity")}
    check = TargetIdentityCheck()
    evidence = check.collect(ssh, config)
    valid, reason = check.validate(evidence, config)
    if not valid:
        raise EvidenceError("target identity does not match baseline: {0}".format(reason))
