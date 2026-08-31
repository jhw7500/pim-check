#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hw_gate.publisher import MAX_EVIDENCE_BYTES, GithubClient, PublisherError, publish_evidence


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish trusted pim-check hardware evidence")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument(
        "--workflow-run-id",
        type=_positive,
        default=os.environ.get("TRIGGERING_WORKFLOW_RUN_ID"),
        required="TRIGGERING_WORKFLOW_RUN_ID" not in os.environ,
    )
    parser.add_argument(
        "--workflow-run-attempt",
        type=_positive,
        default=os.environ.get("TRIGGERING_WORKFLOW_RUN_ATTEMPT"),
        required="TRIGGERING_WORKFLOW_RUN_ATTEMPT" not in os.environ,
    )
    return parser


def _read_evidence(path: Path) -> bytes:
    with path.open("rb") as stream:
        payload = stream.read(MAX_EVIDENCE_BYTES + 1)
    if len(payload) > MAX_EVIDENCE_BYTES:
        raise PublisherError("evidence JSON exceeds 1,048,576 bytes")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        artifact = _read_evidence(args.evidence)
        result = publish_evidence(
            client=GithubClient(token),
            repository=repository,
            github_repository=repository,
            workflow_run_id=args.workflow_run_id,
            workflow_run_attempt=args.workflow_run_attempt,
            evidence_bytes=artifact,
            artifact_root=args.artifact_root,
        )
    except (OSError, PublisherError) as exc:
        print("publish_hw_evidence: {0}".format(exc), file=sys.stderr)
        return 2
    print("{0} PR #{1} hardware evidence ({2})".format(result.action, result.pr_number, result.verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
