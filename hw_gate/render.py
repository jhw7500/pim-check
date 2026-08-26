from __future__ import annotations

import html
import json
import re
from typing import Iterable, List


_MARKDOWN_CONTROL_RE = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")


def _escape(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return _MARKDOWN_CONTROL_RE.sub(r"\\\1", html.escape(text, quote=True))


def _table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> List[str]:
    header = list(headers)
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(_escape(value) for value in row) + " |" for row in rows)
    return lines


def render_markdown(document: dict) -> str:
    """Render one stable, fully escaped phase-one evidence report."""
    run = document.get("run", {}) if isinstance(document.get("run"), dict) else {}
    baseline = document.get("baseline", {}) if isinstance(document.get("baseline"), dict) else {}
    board = document.get("board", {}) if isinstance(document.get("board"), dict) else {}
    verdict = document.get("overall_verdict", document.get("verdict", "ERROR"))
    lines = [
        "# Hardware evidence: {0}".format(_escape(verdict)),
        "",
        "Scope: predeployed measurement (deployment verified=false)",
        "",
        "- PR HEAD: {0}".format(_escape(run.get("pr_head_sha", "unavailable"))),
        "- Baseline SHA256: {0}".format(_escape(baseline.get("sha256", "unavailable"))),
        "- Baseline source commit: {0}".format(_escape(baseline.get("source_commit", "unavailable"))),
        "- Target: {0} at {1}".format(_escape(board.get("id", "pim")), _escape(board.get("target_host", "unavailable"))),
        "- Run URL: [workflow run]({0})".format(run.get("run_url", "unavailable")),
        "",
        "## Target identities",
        "",
    ]
    identities = board.get("identity", []) if isinstance(board.get("identity"), list) else []
    if identities:
        lines.extend(_table(
            ("ID", "Kind", "Expected", "Actual", "Path/module"),
            (
                (
                    identity.get("id", ""), identity.get("kind", ""), identity.get("expected", ""),
                    identity.get("actual", ""), identity.get("path", identity.get("module", "")),
                )
                for identity in identities if isinstance(identity, dict)
            ),
        ))
    else:
        lines.append("No target identity evidence was available.")

    lines.extend(["", "## Metrics", ""])
    metric_rows = []
    precondition_rows = []
    restoration_rows = []
    for gate in document.get("gates", []):
        if not isinstance(gate, dict):
            continue
        gate_id = gate.get("id", "")
        for metric in gate.get("metrics", []):
            if isinstance(metric, dict):
                metric_rows.append((
                    gate_id, metric.get("id", ""), metric.get("value", ""), metric.get("unit", ""),
                    metric.get("baseline_value", ""), metric.get("rule", {}), metric.get("delta", {}),
                    metric.get("verdict", ""),
                ))
        for precondition in gate.get("preconditions", []):
            if isinstance(precondition, dict):
                precondition_rows.append((
                    gate_id, precondition.get("id", ""), precondition.get("expected", ""),
                    precondition.get("observed", ""), precondition.get("verdict", ""),
                ))
        restoration = gate.get("restoration", {})
        if isinstance(restoration, dict):
            restoration_rows.append((
                gate_id, restoration.get("verdict", ""), restoration.get("before_sha256", ""),
                restoration.get("after_sha256", ""), restoration.get("cycles", ""),
            ))
    if metric_rows:
        lines.extend(_table(
            ("Gate", "Metric", "Value", "Unit", "Baseline", "Rule", "Delta", "Verdict"), metric_rows,
        ))
    else:
        lines.append("No numeric metrics were available.")

    lines.extend(["", "## Preconditions", ""])
    if precondition_rows:
        lines.extend(_table(("Gate", "ID", "Expected", "Observed", "Verdict"), precondition_rows))
    else:
        lines.append("No preconditions were recorded.")

    lines.extend(["", "## Restoration", ""])
    if restoration_rows:
        lines.extend(_table(("Gate", "Verdict", "Before SHA", "After SHA", "Cycles"), restoration_rows))
    else:
        lines.append("No restoration evidence was recorded.")

    lines.extend(["", "## Diagnostics", ""])
    diagnostics = document.get("diagnostics", []) if isinstance(document.get("diagnostics"), list) else []
    if diagnostics:
        lines.extend(_table(
            ("ID", "Bounded output"),
            ((item.get("id", ""), item.get("output", "")) for item in diagnostics if isinstance(item, dict)),
        ))
    else:
        lines.append("No diagnostics were recorded.")
    return "\n".join(lines) + "\n"
