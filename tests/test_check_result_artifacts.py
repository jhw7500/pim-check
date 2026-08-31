"""Runtime result JSON staging policy regression tests.

The hook is exercised against real temporary Git repositories so these tests
cover the index behavior that matters: force-added runtime artifacts are
rejected, golden fixtures remain allowed, and removing legacy tracked
artifacts from the index is not blocked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_result_artifacts.py"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "pim-check test")
    _git(repo, "config", "user.email", "pim-check@example.invalid")
    return repo


def _write(repo: Path, relative_path: str) -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")


def _run_checker(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("artifact", ["camera_results.json", "temp_awb_retry.json"])
def test_rejects_staged_root_runtime_result_artifact(tmp_path: Path, artifact: str) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, artifact)
    _git(repo, "add", "--force", "--", artifact)

    result = _run_checker(repo)

    assert result.returncode == 1
    assert "Runtime result JSON artifacts must not be committed:" in result.stderr
    assert f"  - {artifact}" in result.stderr


def test_allows_golden_and_nested_result_json(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    allowed = [
        "tests/golden/comprehensive_results.golden.json",
        "tests/fixtures/archive_results.json",
    ]
    for path in allowed:
        _write(repo, path)
    _git(repo, "add", "--force", "--", *allowed)

    result = _run_checker(repo)

    assert result.returncode == 0
    assert result.stderr == ""


def test_allows_removing_legacy_result_from_index(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    artifact = "legacy_results.json"
    _write(repo, artifact)
    _git(repo, "add", "--", artifact)
    _git(repo, "commit", "--quiet", "-m", "track legacy result")
    _git(repo, "rm", "--cached", "--quiet", "--", artifact)

    result = _run_checker(repo)

    assert result.returncode == 0
    assert (repo / artifact).is_file()


def test_gitignore_ignores_root_artifacts_but_keeps_nested_fixtures(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text((ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8")
    for path in [
        "camera_results.json",
        "temp_awb_retry.json",
        "tests/golden/comprehensive_results.golden.json",
        "tests/fixtures/archive_results.json",
    ]:
        _write(repo, path)

    assert _git(repo, "check-ignore", "--quiet", "camera_results.json", check=False).returncode == 0
    assert _git(repo, "check-ignore", "--quiet", "temp_awb_retry.json", check=False).returncode == 0
    assert (
        _git(
            repo,
            "check-ignore",
            "--quiet",
            "tests/golden/comprehensive_results.golden.json",
            check=False,
        ).returncode
        == 1
    )
    assert _git(repo, "check-ignore", "--quiet", "tests/fixtures/archive_results.json", check=False).returncode == 1
