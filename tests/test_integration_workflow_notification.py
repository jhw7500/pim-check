"""주간 comprehensive 실패가 실제 webhook 호출 경로까지 연결되는지 고정한다."""
from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "hw-verify-comprehensive.yml"


def test_comprehensive_failure_step_invokes_notifier_with_secret():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    steps = jobs["comprehensive"]["steps"]

    cleanup_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Clear stale results cache (avoid fake-success resume)"
    )
    checkout_index = next(
        index for index, step in enumerate(steps) if step.get("name") == "Checkout"
    )
    assert cleanup_index < checkout_index

    assert not [step for step in steps if step.get("name") == "Notify failure"]

    notify_job = jobs["notify_failure"]
    assert notify_job["needs"] == "comprehensive"
    assert notify_job["if"] == "${{ always() && needs.comprehensive.result != 'success' }}"
    assert notify_job["runs-on"] == "ubuntu-latest"

    notify_steps = [
        step for step in notify_job["steps"] if step.get("name") == "Notify failure"
    ]

    assert len(notify_steps) == 1, "comprehensive failure must have one notification step"
    notify = notify_steps[0]
    assert notify.get("continue-on-error") is True
    assert notify.get("env", {}).get("PIM_CHECK_WEBHOOK_URL") == "${{ secrets.PIM_CHECK_WEBHOOK_URL }}"
    assert "python3 notifier.py" in notify.get("run", "")
    assert "--results notification-results/comprehensive_results.json" in notify.get("run", "")

    download_steps = [
        step
        for step in notify_job["steps"]
        if step.get("name") == "Download comprehensive results"
    ]
    assert len(download_steps) == 1
    download = download_steps[0]
    assert download.get("continue-on-error") is True
    assert download.get("uses") == "actions/download-artifact@v4"
    assert download.get("with", {}).get("path") == "notification-results"
