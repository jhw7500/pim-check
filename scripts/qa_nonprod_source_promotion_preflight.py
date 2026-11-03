#!/usr/bin/env python3
"""Fail-closed NONPROD pim-check source promotion *preflight*.

This module deliberately has NO operation that fetches, stages, copies, merges,
checks out or installs source. It is a pure admission review artifact, not an
owner approval, deployment tool or a board executor.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

PROJECT = "pim-check"
FEATURE = "feat/qa-recovery-ord-vcm-20261026"
_sha40 = re.compile(r"[0-9a-f]{40}\Z")
_sha64 = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_SOURCE_FILES = frozenset({
    "pim_check.py",
    "plan.py",
    "config.py",
    "engine.py",
    "ssh.py",
    "setup.py",
    "checks/frame_flow.py",
    "checks/recording.py",
    "checks/custom.py",
    "profiles/cases/verify_ord_vcm_integration.yaml",
    "profiles/cases/verify_pim_system_live_pipeline.yaml",
    "profiles/plans/pim_system_live_readonly.yaml",
    "scripts/with_pim_board.sh",
})


class PromotionPreflightError(ValueError):
    """The source or target evidence cannot be trusted even for review."""


def _paths(values: Any) -> list[str]:
    if not isinstance(values, list) or any(
        not isinstance(p, str) or not p or p.startswith("/")
        or any(c in ("", ".", "..", ".git") for c in p.split("/"))
        for p in values
    ) or len(set(values)) != len(values):
        raise PromotionPreflightError("INVALID_PATH_COLLECTION")
    return sorted(values)


def analyze(
    source_manifest: dict[str, Any],
    observed_target: dict[str, Any],
    changed_paths: list[str],
) -> dict[str, Any]:
    """Describe read-only source and dirty-worktree risks; NEVER authorize."""
    if not isinstance(source_manifest, dict) or not isinstance(observed_target, dict):
        raise PromotionPreflightError("INVALID_EVIDENCE")
    if source_manifest.get("project") != PROJECT:
        raise PromotionPreflightError("WRONG_PROJECT")
    if source_manifest.get("scope") != "nonproduction_source_ci_only":
        raise PromotionPreflightError("WRONG_SOURCE_SCOPE")
    if source_manifest.get("source_branch") != FEATURE:
        raise PromotionPreflightError("WRONG_SOURCE_BRANCH")
    sha = source_manifest.get("source_sha")
    if not isinstance(sha, str) or not _sha40.fullmatch(sha):
        raise PromotionPreflightError("INVALID_SOURCE_SHA")
    if source_manifest.get("source_candidate_status") != "OWNER_REVIEW_REQUIRED":
        raise PromotionPreflightError("NOT_AN_OWNER_REVIEW_CANDIDATE")
    if any(source_manifest.get(flag) is not False for flag in (
        "source_delivery_completed", "buildserver_deployment_authorized",
        "pim_board_execution_authorized", "owner_registered_profile_created",
    )):
        raise PromotionPreflightError("UNTRUSTED_DEPLOYMENT_AUTHORITY_CLAIM")

    files = source_manifest.get("tracked_files")
    if (not isinstance(files, list) or not files or len(files) > 20000
            or source_manifest.get("tracked_file_count") != len(files)):
        raise PromotionPreflightError("INVALID_SOURCE_FILE_COUNT")
    filenames = []
    for row in files:
        if not isinstance(row, dict):
            raise PromotionPreflightError("INVALID_FILE_METADATA")
        name = row.get("path")
        digest = row.get("sha256")
        count = row.get("size_bytes")
        filenames.append(name)
        if (not isinstance(digest, str) or not _sha64.fullmatch(digest)
                or type(count) is not int or count < 0):
            raise PromotionPreflightError("INVALID_FILE_METADATA")
    paths = _paths(filenames)
    if not _REQUIRED_SOURCE_FILES.issubset(paths):
        raise PromotionPreflightError("MISSING_REQUIRED_E2E_SOURCE")

    if (observed_target.get("project") != PROJECT
            or observed_target.get("branch") != "main"):
        raise PromotionPreflightError("WRONG_RECEIVING_CHECKOUT")
    target_head = observed_target.get("head")
    if not isinstance(target_head, str) or not _sha40.fullmatch(target_head):
        raise PromotionPreflightError("INVALID_TARGET_HEAD")
    fp = observed_target.get("dirty_fingerprint")
    if not isinstance(fp, str) or not _sha64.fullmatch(fp):
        raise PromotionPreflightError("INVALID_TARGET_FINGERPRINT")
    dirty = _paths(observed_target.get("dirty_paths"))
    changed = _paths(changed_paths)
    if not changed:
        raise PromotionPreflightError("NO_SOURCE_CHANGE_EVIDENCE")
    conflicts = sorted(set(dirty).intersection(changed))
    reason_codes = [
        "OWNER_SOURCE_PROMOTION_PROFILE_NOT_REGISTERED",
        "CI_AND_TARGET_EVIDENCE_REQUIRE_TRUSTED_LIVE_RECHECK",
    ]
    if conflicts:
        reason_codes.append("DIRTY_WORKTREE_PATH_CONFLICT")
    if source_manifest.get("required_prior_ci_jobs") != ["lint", "test", "js-smoke"]:
        reason_codes.append("UNEXPECTED_CI_GATE")

    return {
        "schema_version": 1,
        "status": "OWNER_APPROVAL_REQUIRED",
        "owner_promotion_authorized": False,
        "buildserver_changed": False,
        "board_executed": False,
        "source_sha": sha,
        "observed_target_head": target_head,
        "observed_target_dirty_fingerprint": fp,
        "dirty_path_count": len(dirty),
        "changed_path_count": len(changed),
        "dirty_path_conflicts": conflicts,
        "reason_codes": reason_codes,
        "required_next_action": (
            "Owner must authorize an independent, explicitly registered "
            "source-promotion policy with exact CI/commit/target pins. "
            "Do not reroute previous denied staging, reset/stash user WIP, "
            "or confuse this preflight with execution approval."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--target-observation", type=Path, required=True)
    parser.add_argument("--source-changes", type=Path, required=True)
    args = parser.parse_args()
    try:
        src = json.loads(args.source_manifest.read_text(encoding="utf-8"))
        tgt = json.loads(args.target_observation.read_text(encoding="utf-8"))
        changed = json.loads(args.source_changes.read_text(encoding="utf-8"))
        result = analyze(src, tgt, changed)
    except (OSError, ValueError, TypeError) as exc:
        result = {"status": "BLOCKED_INVALID_EVIDENCE",
                  "reason": type(exc).__name__, "owner_promotion_authorized": False}
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    # Never return success to an automated source-promotion caller.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
