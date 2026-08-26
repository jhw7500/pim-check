"""주간 comprehensive 실패가 실제 webhook 호출 경로까지 연결되는지 고정한다."""
from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "hw-verify-comprehensive.yml"


def test_comprehensive_failure_step_invokes_notifier_with_secret():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["comprehensive"]["steps"]
    notify_steps = [step for step in steps if step.get("name") == "Notify failure"]

    assert len(notify_steps) == 1, "comprehensive failure must have one notification step"
    notify = notify_steps[0]
    assert notify.get("if") == "failure()"
    assert notify.get("continue-on-error") is True
    assert notify.get("env", {}).get("PIM_CHECK_WEBHOOK_URL") == "${{ secrets.PIM_CHECK_WEBHOOK_URL }}"
    assert "python3 notifier.py" in notify.get("run", "")
    assert "--results comprehensive_results.json" in notify.get("run", "")
