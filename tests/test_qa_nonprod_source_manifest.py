"""Low-cost pim-check CI evidence contract. All Git actions use a temp fixture.

Never creates a PR, publishes source to a buildserver or executes PIM hardware.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/qa_nonprod_source_manifest.py"
WORKFLOW = ROOT / ".github/workflows/test.yml"

spec = importlib.util.spec_from_file_location("qa_nonprod_source_manifest", SCRIPT)
assert spec is not None and spec.loader is not None
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True, capture_output=True, check=True, timeout=20,
    ).stdout.strip()


@pytest.fixture
def clean_git(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "QA Contract Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    (root / "README.md").write_text("QA source\n", encoding="utf-8")
    (root / "profiles").mkdir()
    (root / "profiles" / "pim_system_live_readonly.yaml").write_text(
        "name: readonly\n", encoding="utf-8"
    )
    _git(root, "add", "README.md", "profiles/pim_system_live_readonly.yaml")
    _git(root, "commit", "-qm", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def test_nonproduction_qa_branch_runs_existing_ci_without_pr():
    # BaseLoader preserves YAML 'on' as a string (unlike YAML-1.1 SafeLoader).
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"),
                         Loader=yaml.BaseLoader)
    assert workflow["on"]["push"]["branches"] == [
        "main", policy.SOURCE_BRANCH
    ]
    assert "pull_request" in workflow["on"]
    jobs = workflow["jobs"]
    assert {"lint", "test", "js-smoke", "nonprod-source-candidate"} <= set(jobs)
    assert jobs["test"]["strategy"]["matrix"]["python-version"] == [
        "3.9", "3.11", "3.12"
    ]
    assert jobs["lint"]["runs-on"] == "ubuntu-24.04"
    assert jobs["test"]["runs-on"] == "ubuntu-24.04"
    assert jobs["js-smoke"]["runs-on"] == "ubuntu-24.04"


def test_evidence_job_requires_all_ci_jobs_and_never_runs_board():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"),
                         Loader=yaml.BaseLoader)
    job = workflow["jobs"]["nonprod-source-candidate"]
    assert job["needs"] == ["lint", "test", "js-smoke"]
    assert job["permissions"] == {"contents": "read"}
    assert job["runs-on"] == "ubuntu-24.04"
    assert policy.SOURCE_BRANCH in job["if"]
    assert "github.event_name == 'push'" in job["if"]
    steps = job["steps"]
    assert steps[0]["with"]["persist-credentials"] == "false"
    assert steps[1]["env"] == {
        "SOURCE_SHA": "${{ github.sha }}",
        "SOURCE_BRANCH": "${{ github.ref_name }}",
    }
    assert "qa_nonprod_source_manifest.py" in steps[1]["run"]
    assert steps[-1]["uses"] == (
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    )
    assert steps[-1]["with"]["path"] == policy.OUTPUT
    assert steps[-1]["with"]["retention-days"] == "14"
    serialized = json.dumps(job)
    for forbidden in (
        "jhw-pr", "pre-pr-tribunal", "sshpass", "with_pim_board",
        "systemctl", "jhw-control", "rsync", "scp", "git push",
        "pim_check.py --plan", "execute_host_command",
    ):
        assert forbidden not in serialized


def test_clean_exact_source_gets_metadata_not_deployment_authority(clean_git):
    root, sha = clean_git
    manifest = policy.collect_manifest(
        root, expected_sha=sha, branch=policy.SOURCE_BRANCH
    )
    assert manifest["project"] == "pim-check"
    assert manifest["scope"] == "nonproduction_source_ci_only"
    assert manifest["source_sha"] == sha
    assert manifest["tracked_file_count"] == 2
    assert [f["path"] for f in manifest["tracked_files"]] == [
        "README.md", "profiles/pim_system_live_readonly.yaml"
    ]
    assert all(len(f["sha256"]) == 64 for f in manifest["tracked_files"])
    assert manifest["source_candidate_status"] == "OWNER_REVIEW_REQUIRED"
    assert manifest["native_ai_reviews_required"] is False
    assert manifest["required_prior_ci_jobs"] == ["lint", "test", "js-smoke"]
    for key in (
        "source_delivery_completed", "buildserver_deployment_authorized",
        "pim_board_execution_authorized", "owner_registered_profile_created",
    ):
        assert manifest[key] is False
    assert _git(root, "status", "--porcelain") == ""


@pytest.mark.parametrize("sha,b", [
    ("0" * 40, policy.SOURCE_BRANCH),
    ("not-a-sha", policy.SOURCE_BRANCH),
    ("A" * 40, policy.SOURCE_BRANCH),
    (None, policy.SOURCE_BRANCH),
    ("VALID", "main"),
    ("VALID", "feat/other"),
])
def test_invalid_sha_or_branch_never_generates_candidate(clean_git, sha, b):
    root, actual = clean_git
    with pytest.raises(policy.ManifestError):
        policy.collect_manifest(
            root, expected_sha=actual if sha == "VALID" else sha, branch=b
        )


@pytest.mark.parametrize("dirty_mode", ["modified", "untracked"])
def test_dirty_source_is_not_a_review_candidate(clean_git, dirty_mode):
    root, sha = clean_git
    if dirty_mode == "modified":
        (root / "README.md").write_text("not committed\n", encoding="utf-8")
    else:
        (root / "other.txt").write_text("not committed\n", encoding="utf-8")
    with pytest.raises(policy.ManifestError, match="WORKTREE_NOT_CLEAN"):
        policy.collect_manifest(
            root, expected_sha=sha, branch=policy.SOURCE_BRANCH
        )


def test_symlinked_git_source_rejected(clean_git):
    root, _ = clean_git
    (root / "linked").symlink_to(root / "README.md")
    _git(root, "add", "linked")
    _git(root, "commit", "-qm", "link fixture")
    new_sha = _git(root, "rev-parse", "HEAD")
    with pytest.raises(policy.ManifestError, match="UNSAFE_TRACKED_FILE"):
        policy.collect_manifest(
            root, expected_sha=new_sha, branch=policy.SOURCE_BRANCH
        )


def test_cli_writes_metadata_only_when_repo_exactly_matches(clean_git):
    root, sha = clean_git
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--source-sha", sha,
         "--branch", policy.SOURCE_BRANCH],
        cwd=root, text=True, capture_output=True, check=False, timeout=20,
    )
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("OWNER_REVIEW_REQUIRED")
    recorded = json.loads((root / policy.OUTPUT).read_text(encoding="utf-8"))
    assert recorded["source_sha"] == sha
    assert recorded["buildserver_deployment_authorized"] is False
    assert recorded["pim_board_execution_authorized"] is False


def test_feature_push_does_not_trigger_costly_review_or_live_board_workflows():
    """Only ordinary Tests may react to this branch push; no bot or board job."""
    other = sorted(WORKFLOW.parent.glob("*.yml"))
    assert len(other) > 1
    for path in other:
        if path == WORKFLOW:
            continue
        workflow = yaml.load(path.read_text(encoding="utf-8"),
                             Loader=yaml.BaseLoader)
        events = workflow.get("on")
        if isinstance(events, dict):
            assert "push" not in events, path.name
        elif isinstance(events, list):
            assert "push" not in events, path.name
        else:
            assert events != "push", path.name
