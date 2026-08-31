"""Regression tests for the workflow TARGET_HOST policy guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "check_workflow_target_hosts.py"


def run_guard(workflow_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD), "--workflow-dir", str(workflow_dir)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_allows_only_the_canonical_measure_target_literal(tmp_path: Path) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "hw-evidence-measure.yml").write_text(
        "run: python3 -m hw_gate measure --target-host 192.168.214.4\n"
    )
    (workflows / "hw-verify.yml").write_text(
        "env:\n  TARGET_HOST: ${{ vars.TARGET_HOST }}\n"
    )

    result = run_guard(workflows)

    assert result.returncode == 0, result.stdout + result.stderr


def test_rejects_literal_in_a_variable_driven_workflow(tmp_path: Path) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "hw-verify.yml").write_text(
        "run: ping -c 2 192.168.0.5\n"
    )

    result = run_guard(workflows)

    assert result.returncode == 1
    assert "hw-verify.yml:1" in result.stdout
    assert "192.168.0.5" in result.stdout


def test_rejects_additional_literal_in_the_canonical_workflow(
    tmp_path: Path,
) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "hw-evidence-measure.yml").write_text(
        "run: python3 -m hw_gate measure --target-host 192.168.214.4\n"
        "precheck: ping -c 2 192.168.0.5\n"
    )

    result = run_guard(workflows)

    assert result.returncode == 1
    assert "hw-evidence-measure.yml:2" in result.stdout
    assert "192.168.0.5" in result.stdout


def test_rejects_wrong_target_literal_in_the_canonical_workflow(
    tmp_path: Path,
) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "hw-evidence-measure.yml").write_text(
        "run: python3 -m hw_gate measure --target-host 192.168.0.5\n"
    )

    result = run_guard(workflows)

    assert result.returncode == 1
    assert "hw-evidence-measure.yml:1" in result.stdout
    assert "192.168.0.5" in result.stdout


def test_repository_workflows_satisfy_target_host_policy() -> None:
    result = run_guard(ROOT / ".github" / "workflows")

    assert result.returncode == 0, result.stdout + result.stderr
