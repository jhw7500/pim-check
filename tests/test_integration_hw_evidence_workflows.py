"""Trust-boundary contracts for the split hardware-evidence workflows."""
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
PURPOSE = (
    "github:${{ github.workflow }}:${{ github.run_id }}:"
    "${{ github.run_attempt }}:hw-evidence"
)


def _workflow(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), "missing workflow: {0}".format(name)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def _command(step: dict) -> str:
    return " ".join(step["run"].replace("\\\n", " ").split())


def test_measurement_trigger_condition_and_permissions_are_read_only() -> None:
    """A fork, unlabeled PR, or write token must not reach the board-facing job."""
    workflow = _workflow("hw-evidence-measure.yml")

    assert workflow["name"] == "Hardware Evidence Measurement"
    assert workflow["run-name"] == (
        "hw-evidence-pr-${{ github.event.pull_request.number || inputs.pr_number }}"
    )
    assert workflow["on"]["pull_request_target"] == {
        "types": ["labeled", "synchronize"]
    }
    dispatch = workflow["on"]["workflow_dispatch"]
    assert set(dispatch) == {"inputs"}
    assert dispatch["inputs"]["pr_number"]["required"] is True
    assert dispatch["inputs"]["pr_number"]["type"] == "number"
    assert workflow["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert workflow["concurrency"] == {
        "group": "pim-target-lock",
        "cancel-in-progress": False,
    }

    assert set(workflow["jobs"]) == {"measure"}
    job = workflow["jobs"]["measure"]
    condition = " ".join(job["if"].split())
    assert "github.event_name == 'pull_request_target'" in condition
    assert (
        "github.event.pull_request.head.repo.full_name == github.repository"
        in condition
    )
    assert (
        "contains(github.event.pull_request.labels.*.name, 'needs-hw-verify')"
        in condition
    )
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert (
        "github.ref_name == github.event.repository.default_branch" in condition
    )
    assert job["runs-on"] == ["self-hosted", "linux", "pim-target"]
    assert "permissions" not in job


def test_measurement_uses_trusted_source_and_one_head_bound_lease() -> None:
    """PR code or an unbound envelope must never execute on the leased runner."""
    workflow = _workflow("hw-evidence-measure.yml")
    job = workflow["jobs"]["measure"]
    steps = job["steps"]

    checkout = _step(job, "Checkout trusted measurement code")
    assert checkout["uses"] == CHECKOUT
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert "github.event.pull_request.head.sha" not in str(checkout)

    prepare = _step(job, "Prepare trusted run envelope")
    leased = _step(job, "Run leased hardware evidence")
    finalize = _step(job, "Finalize hardware evidence")
    upload = _step(job, "Upload hardware evidence")
    assert steps.index(prepare) < steps.index(leased) < steps.index(finalize) < steps.index(upload)

    all_commands = "\n".join(
        step.get("run", "") for step in steps if isinstance(step, dict)
    )
    assert all_commands.count("scripts/with_pim_board.sh") == 1

    prepare_command = _command(prepare)
    assert "python3 -m hw_gate prepare" in prepare_command
    assert '--repository "$REPOSITORY"' in prepare_command
    assert '--pr-number "$PR_NUMBER"' in prepare_command
    assert '--pr-head-sha "$PR_HEAD_SHA"' in prepare_command
    assert '--source-commit "$SOURCE_COMMIT"' in prepare_command
    assert "--baseline baselines/hw-baseline.json" in prepare_command
    assert "--output-dir hw-results" in prepare_command

    leased_command = _command(leased)
    expected_child = (
        'scripts/with_pim_board.sh --for 3h --purpose "{0}" -- '
        'python3 -m hw_gate measure --envelope '
        '"hw-results/${{PR_HEAD_SHA}}.candidate.json" '
        "--target-host 192.168.214.4 --output-dir hw-results"
    ).format(PURPOSE)
    assert expected_child in leased_command
    assert "child_exit=$?" in leased_command
    assert 'exit "$child_exit"' in leased_command
    assert "|| true" not in leased_command

    assert finalize["if"] == "always()"
    finalize_command = _command(finalize)
    assert "python3 -m hw_gate finalize" in finalize_command
    assert '--envelope "hw-results/${PR_HEAD_SHA}.candidate.json"' in finalize_command
    assert '--child-exit-code "${HW_GATE_CHILD_EXIT:-2}"' in finalize_command

    assert upload["if"] == "always()"
    assert upload["uses"] == UPLOAD
    assert upload["with"] == {
        "name": "hw-evidence-${{ github.run_id }}-${{ github.run_attempt }}",
        "path": "hw-results/**",
        "if-no-files-found": "error",
    }


def test_measurement_passes_event_data_as_environment_arguments() -> None:
    """Event-controlled PR metadata must remain data instead of shell source text."""
    workflow = _workflow("hw-evidence-measure.yml")
    job = workflow["jobs"]["measure"]

    resolve = _step(job, "Resolve same-repository PR head")
    assert resolve["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ github.event.pull_request.number || inputs.pr_number }}",
        "REPOSITORY": "${{ github.repository }}",
    }
    assert "${{ github.event" not in resolve["run"]
    assert "${{ inputs" not in resolve["run"]
    assert "from hw_gate.publisher import GithubClient" in resolve["run"]
    assert "gh api" not in resolve["run"]
    assert "jq " not in resolve["run"]

    prepare = _step(job, "Prepare trusted run envelope")
    assert prepare["env"] == {
        "REPOSITORY": "${{ github.repository }}",
        "PR_NUMBER": "${{ github.event.pull_request.number || inputs.pr_number }}",
        "SOURCE_COMMIT": "${{ github.sha }}",
        "WORKFLOW_RUN_ID": "${{ github.run_id }}",
        "WORKFLOW_RUN_ATTEMPT": "${{ github.run_attempt }}",
    }
    assert "${{" not in prepare["run"]


def test_publisher_is_hosted_write_scoped_and_triggered_only_by_measurement() -> None:
    """The write token must exist only on the hosted workflow_run consumer."""
    workflow = _workflow("hw-evidence-publish.yml")

    assert workflow["name"] == "Publish Hardware Evidence"
    assert workflow["on"] == {
        "workflow_run": {
            "workflows": ["Hardware Evidence Measurement"],
            "types": ["completed"],
        }
    }
    assert workflow["permissions"] == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "write",
    }
    assert set(workflow["jobs"]) == {"publish"}
    job = workflow["jobs"]["publish"]
    assert job["runs-on"] == "ubuntu-latest"
    assert "self-hosted" not in str(job["runs-on"])
    assert "permissions" not in job


def test_publisher_downloads_only_triggering_artifact_and_always_runs_trusted_code() -> None:
    """A failed or missing measurement artifact must still reach trusted validation."""
    workflow = _workflow("hw-evidence-publish.yml")
    job = workflow["jobs"]["publish"]

    checkout = _step(job, "Checkout current default-branch publisher")
    assert checkout["uses"] == CHECKOUT
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"

    download = _step(job, "Download triggering hardware evidence")
    assert download["uses"] == DOWNLOAD
    assert download["continue-on-error"] is True
    assert download["with"] == {
        "name": (
            "hw-evidence-${{ github.event.workflow_run.id }}-"
            "${{ github.event.workflow_run.run_attempt }}"
        ),
        "path": "hw-results",
        "run-id": "${{ github.event.workflow_run.id }}",
        "github-token": "${{ github.token }}",
    }

    select = _step(job, "Select downloaded evidence")
    publish = _step(job, "Publish trusted hardware evidence")
    assert select["if"] == "always()"
    assert publish["if"] == "always()"
    assert publish["env"] == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "GITHUB_REPOSITORY": "${{ github.repository }}",
        "TRIGGERING_WORKFLOW_RUN_ID": "${{ github.event.workflow_run.id }}",
        "TRIGGERING_WORKFLOW_RUN_ATTEMPT": (
            "${{ github.event.workflow_run.run_attempt }}"
        ),
    }
    assert "${{" not in publish["run"]
    assert (
        'python3 scripts/publish_hw_evidence.py --evidence "$EVIDENCE_PATH"'
        in _command(publish)
    )
