#!/usr/bin/env python3
"""Offline manifest for a tested nonproduction pim-check source revision.

Runs only on an exact clean Git checkout after ordinary CI lint/test/JS jobs.
Does not use SSH, GitHub APIs, PR/merge, board lease, deployment or a host
execution profile. The output is metadata for a *separate* owner review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

PROJECT = "pim-check"
SOURCE_BRANCH = "feat/qa-recovery-ord-vcm-20261026"
OUTPUT = "nonprod-source-manifest.json"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_FILES = 20000


class ManifestError(ValueError):
    """No source candidate may be produced from untrusted inputs."""


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, timeout=20, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestError("GIT_SOURCE_UNAVAILABLE") from exc
    return result.stdout


def collect_manifest(
    root: Path, *,
    expected_sha: str,
    branch: str,
) -> dict[str, object]:
    """Return a deterministic all-tracked-files source inventory, no writes."""
    if type(expected_sha) is not str or _SHA.fullmatch(expected_sha) is None:
        raise ManifestError("INVALID_SOURCE_SHA")
    if branch != SOURCE_BRANCH:
        raise ManifestError("UNSUPPORTED_BRANCH")
    root = Path(root).resolve(strict=True)
    sha = _git(root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip()
    if sha != expected_sha:
        raise ManifestError("SOURCE_HEAD_MISMATCH")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all").strip():
        raise ManifestError("WORKTREE_NOT_CLEAN")
    names = _git(root, "ls-files", "-z").split(b"\0")
    if names and names[-1] == b"":
        names.pop()
    if not names or len(names) > MAX_FILES:
        raise ManifestError("INVALID_TRACKED_FILE_COUNT")

    files = []
    for raw in names:
        try:
            name = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ManifestError("UNSAFE_TRACKED_FILE") from exc
        parts = name.split("/")
        if (not name or name.startswith("/") or
                any(p in ("", ".", "..", ".git") for p in parts)):
            raise ManifestError("UNSAFE_TRACKED_FILE")
        path = root.joinpath(*parts)
        if path.is_symlink() or not path.is_file():
            raise ManifestError("UNSAFE_TRACKED_FILE")
        stat = path.stat()
        if stat.st_size < 0 or stat.st_size > 128 * 1024 * 1024:
            raise ManifestError("UNSAFE_TRACKED_FILE")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        files.append({"path": name, "sha256": digest.hexdigest(),
                      "size_bytes": stat.st_size})

    if [p["path"] for p in files] != sorted(p["path"] for p in files):
        files.sort(key=lambda p: p["path"])
    return {
        "schema_version": 1,
        "project": PROJECT,
        "scope": "nonproduction_source_ci_only",
        "source_branch": SOURCE_BRANCH,
        "source_sha": expected_sha,
        "tracked_files": files,
        "tracked_file_count": len(files),
        "required_prior_ci_jobs": ["lint", "test", "js-smoke"],
        "source_candidate_status": "OWNER_REVIEW_REQUIRED",
        "native_ai_reviews_required": False,
        "source_delivery_completed": False,
        "buildserver_deployment_authorized": False,
        "pim_board_execution_authorized": False,
        "owner_registered_profile_created": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()
    try:
        payload = collect_manifest(
            Path.cwd(), expected_sha=args.source_sha, branch=args.branch
        )
    except (ManifestError, OSError, UnicodeError) as exc:
        parser.exit(3, "BLOCK: " + str(exc) + "\n")
    Path(OUTPUT).write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("OWNER_REVIEW_REQUIRED source=" + args.source_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
