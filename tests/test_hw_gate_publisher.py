from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


REPOSITORY = "jhw7500/pim-check"
RUN_ID = 987654
RUN_ATTEMPT = 2
PR_NUMBER = 115
PR_HEAD_SHA = "a" * 40
SOURCE_SHA = "f" * 40
WORKFLOW_NAME = "Hardware Evidence Measurement"
MARKER = "<!-- pim-check:hardware-evidence -->"
ROOT = Path(__file__).parents[1]
BASELINE_BYTES = (ROOT / "baselines" / "hw-baseline.json").read_bytes()
PASS_FIXTURE = ROOT / "tests" / "fixtures" / "hw_gate" / "evidence_pass.json"


class FakeGithubClient:
    """In-memory GitHub HTTP boundary; publisher behavior remains real."""

    def __init__(self, *, event: str = "pull_request_target", comments: Optional[List[dict]] = None) -> None:
        self.run = {
            "id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "name": WORKFLOW_NAME,
            "display_title": "hw-evidence-pr-115",
            "event": event,
            "head_sha": SOURCE_SHA,
            "actor": {"login": "maintainer"},
            "repository": {"full_name": REPOSITORY},
            "pull_requests": [{"number": PR_NUMBER}] if event == "pull_request_target" else [],
            "html_url": "https://github.com/{0}/actions/runs/{1}".format(REPOSITORY, RUN_ID),
        }
        self.pull = {
            "number": PR_NUMBER,
            "head": {"sha": PR_HEAD_SHA, "repo": {"full_name": REPOSITORY}},
        }
        self.repository = {"full_name": REPOSITORY, "default_branch": "main"}
        self.compare = {"status": "ahead"}
        self.permission = {"permission": "write"}
        self.comments = list(comments or [])
        self.baseline_bytes = BASELINE_BYTES
        self.mutations: List[tuple[str, str, dict]] = []

    def get_json(self, path: str) -> Any:
        if path == "repos/{0}/actions/runs/{1}".format(REPOSITORY, RUN_ID):
            return copy.deepcopy(self.run)
        if path == "repos/{0}/pulls/{1}".format(REPOSITORY, PR_NUMBER):
            return copy.deepcopy(self.pull)
        if path == "repos/{0}".format(REPOSITORY):
            return copy.deepcopy(self.repository)
        if path.startswith("repos/{0}/compare/".format(REPOSITORY)):
            return copy.deepcopy(self.compare)
        if path == "repos/{0}/collaborators/maintainer/permission".format(REPOSITORY):
            return copy.deepcopy(self.permission)
        raise AssertionError("unexpected GitHub GET: {0}".format(path))

    def get_bytes(self, path: str, *, max_bytes: int) -> bytes:
        assert path == "repos/{0}/contents/baselines/hw-baseline.json?ref={1}".format(REPOSITORY, SOURCE_SHA)
        assert max_bytes == 1_048_576
        return self.baseline_bytes

    def get_paginated(self, path: str) -> List[Any]:
        assert path == "repos/{0}/issues/{1}/comments".format(REPOSITORY, PR_NUMBER)
        return copy.deepcopy(self.comments)

    def post_json(self, path: str, payload: dict) -> dict:
        self.mutations.append(("POST", path, copy.deepcopy(payload)))
        return {"id": 9001, **payload}

    def patch_json(self, path: str, payload: dict) -> dict:
        self.mutations.append(("PATCH", path, copy.deepcopy(payload)))
        return {"id": int(path.rsplit("/", 1)[-1]), **payload}


def evidence_bytes(**run_overrides: object) -> bytes:
    document = json.loads(PASS_FIXTURE.read_text(encoding="utf-8"))
    document["source_commit"] = SOURCE_SHA
    document["run"] = {
        "repository": REPOSITORY,
        "pr_number": PR_NUMBER,
        "pr_head_sha": PR_HEAD_SHA,
        "workflow_run_id": RUN_ID,
        "workflow_run_attempt": RUN_ATTEMPT,
        "run_url": "https://github.com/{0}/actions/runs/{1}/attempts/{2}".format(
            REPOSITORY, RUN_ID, RUN_ATTEMPT,
        ),
        **run_overrides,
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def publish(client: FakeGithubClient, artifact: bytes):
    from hw_gate.publisher import publish_evidence

    return publish_evidence(
        client=client,
        repository=REPOSITORY,
        github_repository=REPOSITORY,
        workflow_run_id=RUN_ID,
        workflow_run_attempt=RUN_ATTEMPT,
        evidence_bytes=artifact,
    )


def mutation_body(client: FakeGithubClient) -> str:
    assert len(client.mutations) == 1
    return client.mutations[0][2]["body"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("repository", {"full_name": "attacker/pim-check"}, "repository"),
        ("name", "Attacker Workflow", "workflow"),
        ("display_title", "hw-evidence-pr-115-extra", "run name"),
        ("display_title", None, "run name"),
        ("event", "push", "event"),
        ("event", [], "event"),
        ("id", RUN_ID + 1, "run ID"),
        ("run_attempt", RUN_ATTEMPT + 1, "attempt"),
    ],
)
def test_untrusted_workflow_metadata_fails_without_comment(
    field: str, value: object, message: str,
) -> None:
    """Trusting a caller/run lookalike could let it select a PR mutation target."""
    from hw_gate.publisher import PublisherError

    client = FakeGithubClient()
    client.run[field] = value

    with pytest.raises(PublisherError, match=message):
        publish(client, evidence_bytes())

    assert client.mutations == []


def test_repository_argument_must_be_the_github_environment_repository() -> None:
    """A repository argument that differs from GITHUB_REPOSITORY must not issue API calls."""
    from hw_gate.publisher import PublisherError, publish_evidence

    client = FakeGithubClient()
    with pytest.raises(PublisherError, match="GITHUB_REPOSITORY"):
        publish_evidence(
            client=client,
            repository="attacker/pim-check",
            github_repository=REPOSITORY,
            workflow_run_id=RUN_ID,
            workflow_run_attempt=RUN_ATTEMPT,
            evidence_bytes=evidence_bytes(),
        )
    assert client.mutations == []


def test_missing_github_repository_authority_fails_without_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A direct caller cannot nominate a repository when the trusted environment is absent."""
    from hw_gate.publisher import PublisherError, publish_evidence

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    client = FakeGithubClient()

    with pytest.raises(PublisherError, match="GITHUB_REPOSITORY.*required"):
        publish_evidence(
            client=client,
            repository=REPOSITORY,
            workflow_run_id=RUN_ID,
            workflow_run_attempt=RUN_ATTEMPT,
            evidence_bytes=evidence_bytes(),
        )
    assert client.mutations == []


@pytest.mark.parametrize("permission", ["read", "triage", "none"])
def test_manual_dispatch_requires_write_or_higher_actor_permission(permission: str) -> None:
    """A read-only actor must not turn manual dispatch into a PR write primitive."""
    from hw_gate.publisher import PublisherError

    client = FakeGithubClient(event="workflow_dispatch")
    client.permission = {"permission": permission}

    with pytest.raises(PublisherError, match="permission"):
        publish(client, evidence_bytes())
    assert client.mutations == []


@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_manual_dispatch_requires_source_sha_on_default_branch(status: str) -> None:
    """A source commit not contained by the default branch is untrusted publisher code."""
    from hw_gate.publisher import PublisherError

    client = FakeGithubClient(event="workflow_dispatch")
    client.compare = {"status": status}

    with pytest.raises(PublisherError, match="default branch"):
        publish(client, evidence_bytes())
    assert client.mutations == []


@pytest.mark.parametrize("permission", ["write", "maintain", "admin"])
def test_manual_dispatch_accepts_default_branch_source_and_authorized_actor(permission: str) -> None:
    """Rejecting GitHub's write-or-higher permission vocabulary would block trusted dispatches."""
    client = FakeGithubClient(event="workflow_dispatch")
    client.permission = {"permission": permission}

    result = publish(client, evidence_bytes())

    assert result.verdict == "PASS"
    assert client.mutations[0][0] == "POST"


@pytest.mark.parametrize(
    "associations",
    [
        [],
        {},
        "",
        [{"number": 116}],
        [{"number": []}],
        [{"number": True}],
        [{}],
    ],
)
def test_supplied_workflow_run_pull_requests_must_be_well_typed_and_contain_destination(
    associations: object,
) -> None:
    """Associated PR metadata must not disagree with the strict trusted run name."""
    from hw_gate.publisher import PublisherError

    client = FakeGithubClient()
    client.run["pull_requests"] = associations

    with pytest.raises(PublisherError, match="pull_requests"):
        publish(client, evidence_bytes())
    assert client.mutations == []


def test_absent_workflow_run_pull_requests_is_allowed() -> None:
    """GitHub omitting optional association metadata must not block the strict run-name binding."""
    client = FakeGithubClient()
    del client.run["pull_requests"]

    result = publish(client, evidence_bytes())

    assert result.verdict == "PASS"


def test_pull_request_target_requires_same_repository_head() -> None:
    """A fork PR must not reach the privileged marker publication path."""
    from hw_gate.publisher import PublisherError

    client = FakeGithubClient()
    client.pull["head"]["repo"]["full_name"] = "fork/pim-check"

    with pytest.raises(PublisherError, match="same-repository"):
        publish(client, evidence_bytes())
    assert client.mutations == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"repository": "attacker/pim-check"}, "repository"),
        ({"pr_number": 116}, "PR number"),
        ({"workflow_run_id": RUN_ID + 1}, "run ID"),
        ({"workflow_run_attempt": RUN_ATTEMPT + 1}, "attempt"),
        ({"run_url": "https://example.invalid/run"}, "run URL"),
    ],
)
def test_artifact_internal_binding_mismatch_publishes_error(
    overrides: Dict[str, object], message: str,
) -> None:
    """Artifact identity is comparison-only after the API has established the destination."""
    client = FakeGithubClient()

    result = publish(client, evidence_bytes(**overrides))

    assert result.verdict == "ERROR"
    body = mutation_body(client)
    assert "Hardware evidence: ERROR" in body
    assert message in body


def test_trusted_current_head_difference_replaces_pass_with_stale() -> None:
    """A previously valid measurement must not continue presenting PASS after a push."""
    comments = [{
        "id": 41,
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "body": MARKER + "\n# Hardware evidence: PASS\nold",
    }]
    client = FakeGithubClient(comments=comments)
    client.pull["head"]["sha"] = "b" * 40

    result = publish(client, evidence_bytes())

    assert result.verdict == "STALE"
    method, path, payload = client.mutations[0]
    assert (method, path) == ("PATCH", "repos/{0}/issues/comments/41".format(REPOSITORY))
    assert "Hardware evidence: STALE" in payload["body"]
    assert "Hardware evidence: PASS" not in payload["body"]


def test_oversized_evidence_publishes_bounded_error() -> None:
    """Trailing padding must not bypass the one-MiB artifact boundary."""
    client = FakeGithubClient()
    oversized = evidence_bytes() + b" " * 1_048_576

    result = publish(client, oversized)

    assert result.verdict == "ERROR"
    body = mutation_body(client)
    assert "1,048,576 bytes" in body
    assert len(body.encode("utf-8")) < 4096


@pytest.mark.parametrize("field", ["sha256", "source_commit"])
def test_baseline_binding_mismatch_publishes_error(field: str) -> None:
    """Evidence cannot substitute baseline bytes or calibration provenance."""
    client = FakeGithubClient()
    document = json.loads(evidence_bytes())
    document["baseline"][field] = ("0" * 64) if field == "sha256" else ("0" * 40)

    result = publish(client, json.dumps(document).encode("utf-8"))

    assert result.verdict == "ERROR"
    assert "baseline {0}".format("SHA256" if field == "sha256" else "source commit") in mutation_body(client)


def test_evidence_source_commit_must_match_trusted_workflow_head() -> None:
    """Evidence produced by different publisher code must not inherit the trusted run identity."""
    client = FakeGithubClient()
    document = json.loads(evidence_bytes())
    document["source_commit"] = "e" * 40

    result = publish(client, json.dumps(document).encode("utf-8"))

    assert result.verdict == "ERROR"
    assert "source commit binding" in mutation_body(client)


def test_baseline_is_fetched_at_trusted_workflow_head() -> None:
    """Using publisher HEAD instead of measurement source could silently change policy."""
    client = FakeGithubClient()

    publish(client, evidence_bytes())

    assert client.mutations


def test_producer_publisher_verdict_disagreement_publishes_error() -> None:
    """A producer declaration cannot override current publisher rule evaluation."""
    client = FakeGithubClient()
    document = json.loads(evidence_bytes())
    document["verdict"] = "FAIL"
    document["overall_verdict"] = "FAIL"

    result = publish(client, json.dumps(document).encode("utf-8"))

    assert result.verdict == "ERROR"
    assert "trusted recomputation" in mutation_body(client)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rule", {"kind": "exact", "reference": 1020657}),
        ("delta", {"absolute": 123, "percent": 123}),
        ("verdict", "FAIL"),
    ],
)
def test_metric_canonical_fields_must_match_trusted_baseline_evaluation(
    field: str,
    value: object,
) -> None:
    """Artifact-controlled metric presentation must not survive trusted overall recomputation."""
    client = FakeGithubClient()
    document = json.loads(evidence_bytes())
    metric = document["gates"][0]["metrics"][0]
    metric[field] = value

    result = publish(client, json.dumps(document).encode("utf-8"))

    assert result.verdict == "ERROR"
    body = mutation_body(client)
    assert "metric bps.ch0.1024.baseline {0}".format(field) in body
    assert "trusted baseline evaluation" in body


def test_malformed_artifact_with_trusted_destination_publishes_bounded_error() -> None:
    """A broken download still needs an actionable ERROR on the API-bound PR."""
    client = FakeGithubClient()

    result = publish(client, b'{"diagnostic":"<script>"')

    assert result.verdict == "ERROR"
    body = mutation_body(client)
    assert "Hardware evidence: ERROR" in body
    assert len(body.encode("utf-8")) < 4096
    assert "<script>" not in body


def test_malformed_artifact_without_trusted_destination_never_mutates_api() -> None:
    """Malformed artifact data must never be consulted to recover a destination."""
    from hw_gate.publisher import PublisherError

    client = FakeGithubClient()
    client.run["display_title"] = "untrusted-title"

    with pytest.raises(PublisherError, match="run name"):
        publish(client, b'{"run":{"pr_number":116}')
    assert client.mutations == []


def test_marker_comment_is_created_when_absent_and_is_compact_but_complete() -> None:
    """Dropping any audit section would make the durable comment insufficient for review."""
    client = FakeGithubClient()

    result = publish(client, evidence_bytes())

    assert result.verdict == "PASS"
    method, path, payload = client.mutations[0]
    assert (method, path) == ("POST", "repos/{0}/issues/{1}/comments".format(REPOSITORY, PR_NUMBER))
    body = payload["body"]
    assert body.startswith(MARKER + "\n")
    for required in (
        "Hardware evidence: PASS", "predeployed measurement", PR_HEAD_SHA,
        "actions/runs/{0}/attempts/{1}".format(RUN_ID, RUN_ATTEMPT),
        "Baseline SHA256", "Target identities", "Metrics", "Rule", "Delta",
        "Preconditions", "Restoration", "Diagnostics",
    ):
        assert required in body
    assert len(body.encode("utf-8")) < 65_536


def test_one_bot_owned_marker_comment_is_updated() -> None:
    """Creating duplicates instead of updating the owned marker breaks idempotence."""
    comments = [{
        "id": 42,
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "body": "prefix\n" + MARKER + "\nold",
    }]
    client = FakeGithubClient(comments=comments)

    publish(client, evidence_bytes())

    assert client.mutations[0][:2] == ("PATCH", "repos/{0}/issues/comments/42".format(REPOSITORY))


def test_another_authors_marker_lookalike_is_not_updated() -> None:
    """A user-authored marker lookalike must not be overwritten by automation."""
    comments = [{
        "id": 43,
        "user": {"login": "contributor", "type": "User"},
        "body": MARKER + "\nlookalike",
    }]
    client = FakeGithubClient(comments=comments)

    publish(client, evidence_bytes())

    assert client.mutations[0][:2] == (
        "POST", "repos/{0}/issues/{1}/comments".format(REPOSITORY, PR_NUMBER),
    )


def test_comment_html_escapes_validated_artifact_strings() -> None:
    """Validated measured strings must remain inert in GitHub-rendered HTML."""
    client = FakeGithubClient()
    document = json.loads(evidence_bytes())
    document["diagnostics"] = [{
        "id": "measured.output",
        "output": "<script>alert(1)</script>|**x**",
    }]

    publish(client, json.dumps(document).encode("utf-8"))

    body = mutation_body(client)
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "|**x**" not in body


class FakeResponse:
    def __init__(self, payload: bytes, link: str = "") -> None:
        self.payload = payload
        self.headers = {"Link": link}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def read(self, amount: int = -1) -> bytes:
        return self.payload if amount < 0 else self.payload[:amount]


def test_github_client_follows_api_pagination_with_bounded_json_reads() -> None:
    """Ignoring Link pagination could miss an existing marker and create duplicates."""
    from hw_gate.publisher import GithubClient

    requests = []

    def opener(request, *, timeout: float):
        requests.append((request, timeout))
        if len(requests) == 1:
            return FakeResponse(
                b'[{"id":1}]',
                '<https://api.github.com/repos/jhw7500/pim-check/issues/115/comments?page=2>; rel="next"',
            )
        return FakeResponse(b'[{"id":2}]')

    client = GithubClient(token="secret", opener=opener, timeout=7.0, max_json_bytes=1024)

    assert client.get_paginated("repos/jhw7500/pim-check/issues/115/comments") == [{"id": 1}, {"id": 2}]
    assert len(requests) == 2
    assert all(item[1] == 7.0 for item in requests)
    assert requests[0][0].get_header("Authorization") == "Bearer secret"


def test_github_client_rejects_json_response_over_bound() -> None:
    """A hostile or accidental API payload must not grow memory without limit."""
    from hw_gate.publisher import GithubClient, PublisherError

    def opener(request, *, timeout: float):
        del request, timeout
        return FakeResponse(b"[" + b"0" * 32 + b"]")

    client = GithubClient(token="secret", opener=opener, max_json_bytes=16)

    with pytest.raises(PublisherError, match="size limit"):
        client.get_json("repos/jhw7500/pim-check")


def test_github_client_rejects_cross_origin_pagination_link() -> None:
    """Pagination must not send the GitHub token to a Link-selected external origin."""
    from hw_gate.publisher import GithubClient, PublisherError

    requests = []

    def opener(request, *, timeout: float):
        del timeout
        requests.append(request)
        return FakeResponse(b"[]", '<https://attacker.invalid/page/2>; rel="next"')

    client = GithubClient(token="secret", opener=opener)

    with pytest.raises(PublisherError, match="pagination origin"):
        client.get_paginated("repos/jhw7500/pim-check/issues/115/comments")
    assert len(requests) == 1


def test_thin_script_reads_evidence_with_the_same_one_mib_bound(tmp_path: Path) -> None:
    """The script must reject oversized files before allocating their complete contents."""
    from hw_gate.publisher import PublisherError
    from scripts.publish_hw_evidence import _read_evidence

    artifact = tmp_path / "oversized.json"
    artifact.write_bytes(b"x" * (1_048_576 + 1))

    with pytest.raises(PublisherError, match="1,048,576 bytes"):
        _read_evidence(artifact)
