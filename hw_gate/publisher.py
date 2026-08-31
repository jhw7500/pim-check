from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from .baseline import validate_baseline
from .evidence import recompute_overall_verdict, validate_structure
from .render import render_markdown
from .rules import EvidenceError, evaluate_rule


EXPECTED_WORKFLOW = "Hardware Evidence Measurement"
MARKER = "<!-- pim-check:hardware-evidence -->"
MAX_EVIDENCE_BYTES = 1_048_576
MAX_COMMENT_BYTES = 65_535
_BOT_LOGIN = "github-actions[bot]"
_RUN_NAME_RE = re.compile(r"^hw-evidence-pr-([0-9]+)$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_EVENTS = {"pull_request_target", "workflow_dispatch"}
_WRITE_PERMISSIONS = {"write", "maintain", "admin"}


class PublisherError(RuntimeError):
    """Raised when trusted workflow metadata cannot establish a destination."""


@dataclass(frozen=True)
class PublishResult:
    verdict: str
    pr_number: int
    action: str


class GithubClient:
    """Small bounded GitHub REST client used only by the hosted publisher."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        timeout: float = 15.0,
        max_json_bytes: int = MAX_EVIDENCE_BYTES,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not token:
            raise PublisherError("GITHUB_TOKEN is required")
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._max_json_bytes = max_json_bytes
        self._opener = opener

    def _url(self, path: str) -> str:
        if path.startswith("https://"):
            if not path.startswith(self._api_url + "/"):
                raise PublisherError("GitHub API pagination origin is not trusted")
            return path
        return self._api_url + "/" + path.lstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        accept: str = "application/vnd.github+json",
        max_bytes: Optional[int] = None,
    ) -> tuple[bytes, Mapping[str, str]]:
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self._url(path),
            data=data,
            method=method,
            headers={
                "Accept": accept,
                "Authorization": "Bearer " + self._token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pim-check-hardware-evidence-publisher",
            },
        )
        limit = self._max_json_bytes if max_bytes is None else max_bytes
        try:
            with self._opener(request, timeout=self._timeout) as response:
                body = response.read(limit + 1)
                headers = response.headers
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise PublisherError("GitHub API request failed") from exc
        if len(body) > limit:
            raise PublisherError("GitHub API response exceeds size limit")
        return body, headers

    @staticmethod
    def _decode_json(body: bytes) -> Any:
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublisherError("GitHub API returned malformed JSON") from exc

    def get_json(self, path: str) -> Any:
        body, _ = self._request("GET", path)
        return self._decode_json(body)

    def get_bytes(self, path: str, *, max_bytes: int) -> bytes:
        body, _ = self._request(
            "GET", path, accept="application/vnd.github.raw+json", max_bytes=max_bytes,
        )
        return body

    def get_paginated(self, path: str) -> List[Any]:
        separator = "&" if "?" in path else "?"
        next_url: Optional[str] = path + separator + "per_page=100"
        items: List[Any] = []
        pages = 0
        while next_url is not None:
            pages += 1
            if pages > 20:
                raise PublisherError("GitHub API pagination exceeds page limit")
            body, headers = self._request("GET", next_url)
            page = self._decode_json(body)
            if not isinstance(page, list):
                raise PublisherError("GitHub paginated response must be a list")
            items.extend(page)
            next_url = _next_link(headers.get("Link", ""))
        return items

    def post_json(self, path: str, payload: dict) -> dict:
        body, _ = self._request("POST", path, payload=payload)
        response = self._decode_json(body)
        if not isinstance(response, dict):
            raise PublisherError("GitHub mutation response must be an object")
        return response

    def patch_json(self, path: str, payload: dict) -> dict:
        body, _ = self._request("PATCH", path, payload=payload)
        response = self._decode_json(body)
        if not isinstance(response, dict):
            raise PublisherError("GitHub mutation response must be an object")
        return response


def _next_link(header: str) -> Optional[str]:
    for value in header.split(","):
        match = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"\s*$', value)
        if match and match.group(2) == "next":
            return match.group(1)
    return None


def _mapping(value: object, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PublisherError("{0} is missing or malformed".format(field))
    return value


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PublisherError("trusted {0} is invalid".format(field))
    return value


def _trusted_destination(
    client: Any,
    repository: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
) -> tuple[dict, dict, int]:
    run_path = "repos/{0}/actions/runs/{1}".format(repository, workflow_run_id)
    run = _mapping(client.get_json(run_path), "workflow run")
    if _positive_integer(run.get("id"), "run ID") != workflow_run_id:
        raise PublisherError("trusted run ID disagrees with requested run ID")
    if _positive_integer(run.get("run_attempt"), "run attempt") != workflow_run_attempt:
        raise PublisherError("trusted workflow attempt disagrees with requested attempt")
    if run.get("name") != EXPECTED_WORKFLOW:
        raise PublisherError("trusted workflow name is not the hardware measurement workflow")
    event = run.get("event")
    if not isinstance(event, str) or event not in _ALLOWED_EVENTS:
        raise PublisherError("trusted workflow event is not allowed")
    display_title = run.get("display_title")
    match = _RUN_NAME_RE.fullmatch(display_title) if isinstance(display_title, str) else None
    if match is None:
        raise PublisherError("trusted run name must match hw-evidence-pr-[0-9]+")
    pr_number = int(match.group(1))
    if pr_number <= 0:
        raise PublisherError("trusted run name contains an invalid PR number")
    run_repository = _mapping(run.get("repository"), "workflow repository")
    if run_repository.get("full_name") != repository:
        raise PublisherError("trusted workflow repository disagrees with GITHUB_REPOSITORY")
    source_sha = run.get("head_sha")
    if not isinstance(source_sha, str) or _SHA_RE.fullmatch(source_sha) is None:
        raise PublisherError("trusted workflow source SHA is malformed")

    if event == "pull_request_target" and "pull_requests" in run:
        associated = run["pull_requests"]
        if not isinstance(associated, list):
            raise PublisherError("workflow_run.pull_requests must be a list")
        associated_numbers = []
        for item in associated:
            if not isinstance(item, dict):
                raise PublisherError("workflow_run.pull_requests entries must be objects")
            associated_numbers.append(_positive_integer(
                item.get("number"), "workflow_run.pull_requests PR number",
            ))
        if pr_number not in associated_numbers:
            raise PublisherError("workflow_run.pull_requests disagrees with trusted run name")

    if event == "workflow_dispatch":
        repository_data = _mapping(client.get_json("repos/{0}".format(repository)), "repository")
        default_branch = repository_data.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise PublisherError("repository default branch is unavailable")
        comparison = _mapping(
            client.get_json("repos/{0}/compare/{1}...{2}".format(
                repository,
                source_sha,
                urllib.parse.quote(default_branch, safe=""),
            )),
            "default branch comparison",
        )
        if comparison.get("status") not in {"ahead", "identical"}:
            raise PublisherError("trusted source SHA is not on the default branch")
        actor = _mapping(run.get("actor"), "workflow actor").get("login")
        if not isinstance(actor, str) or not actor:
            raise PublisherError("trusted workflow actor is missing")
        permission = _mapping(
            client.get_json("repos/{0}/collaborators/{1}/permission".format(
                repository, urllib.parse.quote(actor, safe=""),
            )),
            "actor permission",
        ).get("permission")
        if permission not in _WRITE_PERMISSIONS:
            raise PublisherError("workflow_dispatch actor lacks write permission")

    pull = _mapping(client.get_json("repos/{0}/pulls/{1}".format(repository, pr_number)), "pull request")
    if _positive_integer(pull.get("number"), "PR number") != pr_number:
        raise PublisherError("trusted PR number disagrees with fetched pull request")
    head = _mapping(pull.get("head"), "pull request head")
    current_head = head.get("sha")
    if not isinstance(current_head, str) or _SHA_RE.fullmatch(current_head) is None:
        raise PublisherError("current PR HEAD is malformed")
    if event == "pull_request_target":
        head_repository = _mapping(head.get("repo"), "pull request head repository")
        if head_repository.get("full_name") != repository:
            raise PublisherError("pull_request_target requires a same-repository PR head")
    return run, pull, pr_number


def _parse_evidence(evidence_bytes: bytes) -> dict:
    if len(evidence_bytes) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("evidence JSON exceeds 1,048,576 bytes")
    try:
        document = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("evidence JSON is malformed") from exc
    if not isinstance(document, dict):
        raise EvidenceError("evidence JSON must be an object")
    return document


def _validate_raw_outputs(document: dict, artifact_root: Path) -> None:
    """Bind every declared gate raw digest to one regular downloaded file."""
    try:
        root = artifact_root.resolve(strict=True)
    except OSError as exc:
        raise EvidenceError("raw output artifact root is unavailable") from exc
    if not root.is_dir():
        raise EvidenceError("raw output artifact root is not a directory")

    seen_paths = set()
    for gate in document["gates"]:
        raw_output = gate["raw_output"]
        relative = raw_output["path"]
        if not relative.startswith("raw/") or relative in seen_paths:
            raise EvidenceError("raw output path is outside the unique raw inventory")
        seen_paths.add(relative)
        candidate = root / relative
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise EvidenceError("raw output file is missing or outside the artifact root") from exc
        if resolved != candidate or not resolved.is_file():
            raise EvidenceError("raw output file must be a regular non-symlink artifact")
        digest = hashlib.sha256()
        try:
            with resolved.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65_536), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise EvidenceError("raw output file could not be read") from exc
        if digest.hexdigest() != raw_output["sha256"]:
            raise EvidenceError("raw output SHA256 mismatch")


def _baseline_at_source(client: Any, repository: str, source_sha: str) -> tuple[dict, str]:
    path = "repos/{0}/contents/baselines/hw-baseline.json?ref={1}".format(
        repository, urllib.parse.quote(source_sha, safe=""),
    )
    payload = client.get_bytes(path, max_bytes=MAX_EVIDENCE_BYTES)
    digest = hashlib.sha256(payload).hexdigest()
    try:
        baseline = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("trusted baseline is malformed") from exc
    validate_baseline(baseline, production=True)
    return baseline, digest


def _validate_internal_binding(
    document: dict,
    baseline: dict,
    baseline_digest: str,
    repository: str,
    pr_number: int,
    run: dict,
    workflow_run_attempt: int,
) -> str:
    evidence_run = document.get("run")
    binding = document.get("baseline")
    if not isinstance(evidence_run, dict) or not isinstance(binding, dict):
        raise EvidenceError("evidence run and baseline bindings are required")
    expected = {
        "repository": repository,
        "pr_number": pr_number,
        "workflow_run_id": run["id"],
        "workflow_run_attempt": workflow_run_attempt,
        "run_url": "https://github.com/{0}/actions/runs/{1}/attempts/{2}".format(
            repository, run["id"], workflow_run_attempt,
        ),
    }
    labels = {
        "repository": "repository",
        "pr_number": "PR number",
        "workflow_run_id": "run ID",
        "workflow_run_attempt": "attempt",
        "run_url": "run URL",
    }
    for field, value in expected.items():
        if evidence_run.get(field) != value:
            raise EvidenceError("evidence {0} binding mismatch".format(labels[field]))
    pr_head_sha = evidence_run.get("pr_head_sha")
    if not isinstance(pr_head_sha, str) or _SHA_RE.fullmatch(pr_head_sha) is None:
        raise EvidenceError("evidence PR HEAD binding is malformed")
    if document.get("source_commit") != run["head_sha"]:
        raise EvidenceError("evidence source commit binding mismatch")
    if binding.get("path") != "baselines/hw-baseline.json":
        raise EvidenceError("evidence baseline path binding mismatch")
    if binding.get("sha256") != baseline_digest:
        raise EvidenceError("evidence baseline SHA256 binding mismatch")
    if binding.get("source_commit") != baseline.get("source_commit"):
        raise EvidenceError("evidence baseline source commit binding mismatch")
    return pr_head_sha


def _validate_metric_presentations(document: dict, baseline: dict) -> None:
    baseline_gates = baseline["gates"]
    for gate in document["gates"]:
        baseline_gate = baseline_gates.get(gate["id"])
        if not isinstance(baseline_gate, dict):
            continue
        baseline_metrics = baseline_gate["metrics"]
        for metric in gate["metrics"]:
            baseline_metric = baseline_metrics.get(metric["id"])
            if not isinstance(baseline_metric, dict):
                continue
            expected = evaluate_rule(metric["value"], metric["unit"], baseline_metric)
            for field in ("baseline_value", "rule", "delta", "verdict"):
                if metric.get(field) != expected[field]:
                    raise EvidenceError(
                        "metric {0} {1} disagrees with trusted baseline evaluation".format(
                            metric["id"], field,
                        )
                    )


def _error_comment(run: dict, current_head: str, message: str) -> str:
    bounded = message.replace("\r", " ").replace("\n", " ")[:512]
    run_url = "https://github.com/{0}/actions/runs/{1}/attempts/{2}".format(
        run["repository"]["full_name"], run["id"], run["run_attempt"],
    )
    return "\n".join([
        MARKER,
        "# Hardware evidence: ERROR",
        "",
        "Scope: predeployed measurement (deployment.verified=false)",
        "",
        "- Current PR HEAD: {0}".format(html.escape(current_head, quote=True)),
        "- Run: [workflow run]({0})".format(run_url),
        "- Diagnostic: {0}".format(html.escape(bounded, quote=True)),
        "",
    ])


def _render_comment(document: dict, verdict: str) -> str:
    rendered = document
    if verdict != document.get("verdict") or verdict != document.get("overall_verdict"):
        rendered = dict(document)
        rendered["verdict"] = verdict
        rendered["overall_verdict"] = verdict
    body = MARKER + "\n" + render_markdown(rendered)
    if len(body.encode("utf-8")) > MAX_COMMENT_BYTES:
        raise EvidenceError("rendered evidence comment exceeds GitHub size limit")
    return body


def _upsert_comment(client: Any, repository: str, pr_number: int, body: str) -> str:
    comments_path = "repos/{0}/issues/{1}/comments".format(repository, pr_number)
    for comment in client.get_paginated(comments_path):
        if not isinstance(comment, dict) or MARKER not in comment.get("body", ""):
            continue
        user = comment.get("user")
        comment_id = comment.get("id")
        if (
            isinstance(user, dict)
            and user.get("login") == _BOT_LOGIN
            and user.get("type") == "Bot"
            and isinstance(comment_id, int)
            and not isinstance(comment_id, bool)
        ):
            client.patch_json("repos/{0}/issues/comments/{1}".format(repository, comment_id), {"body": body})
            return "updated"
    client.post_json(comments_path, {"body": body})
    return "created"


def publish_evidence(
    *,
    client: Any,
    repository: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
    evidence_bytes: bytes,
    artifact_root: Optional[Path] = None,
    github_repository: Optional[str] = None,
) -> PublishResult:
    """Validate one triggering run and upsert its evidence on the API-bound PR."""
    environment_repository = github_repository
    if environment_repository is None:
        environment_repository = os.environ.get("GITHUB_REPOSITORY")
    if not environment_repository:
        raise PublisherError("GITHUB_REPOSITORY is required")
    if repository != environment_repository:
        raise PublisherError("repository must equal GITHUB_REPOSITORY")
    if _REPOSITORY_RE.fullmatch(repository) is None:
        raise PublisherError("GITHUB_REPOSITORY is malformed")
    _positive_integer(workflow_run_id, "run ID")
    _positive_integer(workflow_run_attempt, "workflow attempt")
    run, pull, pr_number = _trusted_destination(
        client, repository, workflow_run_id, workflow_run_attempt,
    )
    current_head = pull["head"]["sha"]

    try:
        document = _parse_evidence(evidence_bytes)
        baseline, baseline_digest = _baseline_at_source(client, repository, run["head_sha"])
        measured_head = _validate_internal_binding(
            document,
            baseline,
            baseline_digest,
            repository,
            pr_number,
            run,
            workflow_run_attempt,
        )
        validate_structure(document)
        if artifact_root is None:
            raise EvidenceError("raw output artifact root is required")
        _validate_raw_outputs(document, artifact_root)
        _validate_metric_presentations(document, baseline)
        recomputed = recompute_overall_verdict(document, baseline).value
        if document.get("verdict") != recomputed or document.get("overall_verdict") != recomputed:
            raise EvidenceError("producer verdict disagrees with trusted recomputation")
        verdict = "STALE" if current_head != measured_head else recomputed
        body = _render_comment(document, verdict)
    except (EvidenceError, KeyError, TypeError, ValueError, PublisherError) as exc:
        verdict = "ERROR"
        body = _error_comment(run, current_head, str(exc))

    action = _upsert_comment(client, repository, pr_number, body)
    return PublishResult(verdict=verdict, pr_number=pr_number, action=action)
