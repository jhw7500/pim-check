"""Nonproduction PIM source promotion remains owner-gated: pure preflight tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/qa_nonprod_source_promotion_preflight.py"
SPEC = importlib.util.spec_from_file_location("qa_nonprod_source_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def _source():
    names = sorted(M._REQUIRED_SOURCE_FILES)
    return {
        "project": "pim-check",
        "scope": "nonproduction_source_ci_only",
        "source_branch": M.FEATURE,
        "source_sha": "a" * 40,
        "tracked_files": [
            {"path": name, "sha256": "f" * 64, "size_bytes": 13}
            for name in names
        ],
        "tracked_file_count": len(names),
        "source_candidate_status": "OWNER_REVIEW_REQUIRED",
        "required_prior_ci_jobs": ["lint", "test", "js-smoke"],
        "source_delivery_completed": False,
        "buildserver_deployment_authorized": False,
        "pim_board_execution_authorized": False,
        "owner_registered_profile_created": False,
    }


def _target():
    return {
        "project": "pim-check",
        "branch": "main",
        "head": "b" * 40,
        "dirty_fingerprint": "c" * 64,
        "dirty_paths": ["AGENTS.md", "pim_viewer.py"],
    }


def test_exact_conflicting_dirty_path_always_blocks_and_preserves_inputs():
    src, tgt = _source(), _target()
    original = json.dumps({"s": src, "t": tgt}, sort_keys=True)
    result = M.analyze(src, tgt, ["AGENTS.md", "pim_check.py"])
    assert result["status"] == "OWNER_APPROVAL_REQUIRED"
    assert result["owner_promotion_authorized"] is False
    assert result["buildserver_changed"] is False
    assert result["board_executed"] is False
    assert result["dirty_path_conflicts"] == ["AGENTS.md"]
    assert result["dirty_path_count"] == 2
    assert "DIRTY_WORKTREE_PATH_CONFLICT" in result["reason_codes"]
    assert "OWNER_SOURCE_PROMOTION_PROFILE_NOT_REGISTERED" in result["reason_codes"]
    assert original == json.dumps({"s": src, "t": tgt}, sort_keys=True)


def test_even_disjoint_dirty_source_cannot_be_deployed_by_preflight():
    r = M.analyze(_source(), _target(), ["plan.py"])
    assert r["dirty_path_conflicts"] == []
    assert r["status"] == "OWNER_APPROVAL_REQUIRED"
    assert r["owner_promotion_authorized"] is False
    assert r["reason_codes"][0] == "OWNER_SOURCE_PROMOTION_PROFILE_NOT_REGISTERED"


@pytest.mark.parametrize("field,value", [
    ("project", "production"),
    ("scope", "production-release"),
    ("source_branch", "main"),
    ("source_sha", "HEAD"),
    ("source_sha", "Z" * 40),
    ("source_candidate_status", "APPROVED"),
    ("source_delivery_completed", True),
    ("buildserver_deployment_authorized", True),
    ("pim_board_execution_authorized", True),
    ("owner_registered_profile_created", True),
])
def test_untrusted_source_or_deployment_approval_is_rejected(field, value):
    source = _source()
    source[field] = value
    with pytest.raises(M.PromotionPreflightError):
        M.analyze(source, _target(), ["plan.py"])


@pytest.mark.parametrize("target_update", [
    {"project": "gstApp"},
    {"branch": "feature"},
    {"head": "bad"},
    {"dirty_fingerprint": "bad"},
    {"dirty_paths": ["../escape"]},
    {"dirty_paths": ["/tmp/other"]},
    {"dirty_paths": ["AGENTS.md", "AGENTS.md"]},
])
def test_unsafe_target_snapshot_rejected(target_update):
    target = _target()
    target.update(target_update)
    with pytest.raises(M.PromotionPreflightError):
        M.analyze(_source(), target, ["plan.py"])


@pytest.mark.parametrize("changed", [
    [], ["../main"], ["/root/x"], ["x", "x"], ["a//b"],
])
def test_invalid_changed_paths_rejected(changed):
    with pytest.raises(M.PromotionPreflightError):
        M.analyze(_source(), _target(), changed)


def test_missing_actual_s0_plan_is_hard_failure():
    source = _source()
    source["tracked_files"] = [
        f for f in source["tracked_files"]
        if f["path"] != "profiles/plans/pim_system_live_readonly.yaml"
    ]
    source["tracked_file_count"] = len(source["tracked_files"])
    with pytest.raises(M.PromotionPreflightError, match="MISSING_REQUIRED_E2E_SOURCE"):
        M.analyze(source, _target(), ["plan.py"])


def test_pure_script_cli_does_not_write_any_files(tmp_path):
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    changed = tmp_path / "changes.json"
    source.write_text(json.dumps(_source()))
    target.write_text(json.dumps(_target()))
    changed.write_text(json.dumps(["AGENTS.md", "plan.py"]))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--source-manifest", str(source),
            "--target-observation", str(target),
            "--source-changes", str(changed),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["owner_promotion_authorized"] is False
    assert payload["buildserver_changed"] is False
    assert "DIRTY_WORKTREE_PATH_CONFLICT" in payload["reason_codes"]
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_cli_invalid_evidence_reports_no_authority(tmp_path):
    for name in ("source", "target", "changes"):
        (tmp_path / f"{name}.json").write_text("{}")
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--source-manifest", str(tmp_path / "source.json"),
         "--target-observation", str(tmp_path / "target.json"),
         "--source-changes", str(tmp_path / "changes.json")],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert r.returncode == 2
    assert json.loads(r.stdout) == {
        "status": "BLOCKED_INVALID_EVIDENCE",
        "reason": "PromotionPreflightError",
        "owner_promotion_authorized": False,
    }
