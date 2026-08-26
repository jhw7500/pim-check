# Hardware Evidence Gate Design

**Status:** Approved
**Date:** 2026-08-26
**Task:** `tsk-01a03ce1-5095-7930-8989-9d90be635fb8`
**Issue:** [#115](https://github.com/jhw7500/pim-check/issues/115)

## Context

pim-check can already reserve the PIM target and run hardware workflows, but
the workflows treat a successful process exit as their primary success signal.
The legacy hardware runners emit runner-specific JSON, leave numeric evidence
in different shapes, and do not publish a durable, current-HEAD-bound evidence
block to a pull request. `run_bps_quick.py` also changes only bitrate and uses a
warm `killcam` restart, while encoder state left by earlier runs can change the
observed bitrate. `run_mixed_combo_verify.py` changes the target repeatedly and
does not restore the original configuration.

Issue #115 asks for an always-available hardware gate that owns the board
lease, measures the target, compares every gate with a committed baseline, and
publishes the measurements to a pull request. The central invariant is:

> A process exit code is diagnostic metadata, never sufficient PASS evidence.

This design covers the first vertical slice: trusted measurement and evidence
publication for an already-deployed board. Automatic DTB and kernel-module
deployment is deliberately deferred behind an explicit deployment-adapter
contract. A phase-one PASS therefore means that the recorded target identity
and measurements passed; it does not claim that the PR's code was deployed.

## Goals

- Hold one explicit exclusive PIM board lease for the entire measurement
  matrix and preserve busy as visible exit status 4.
- Convert gate-specific output into one canonical, validated evidence schema.
- Require at least one measured, baseline-asserted value for every gate.
- Make BPS measurements reproducible by controlling encoder configuration,
  rebooting after changes, and restoring the exact original configuration.
- Compare target artifact identities and measurements with a committed
  `baselines/hw-baseline.json` file.
- Upload JSON and Markdown evidence for every terminal outcome, including
  partial failures.
- Publish one current-HEAD-bound PR comment without granting the self-hosted
  measurement job PR write permission.
- Leave a narrow adapter interface for later DTB/module deployment and new
  jitter, WHOAMI, skew, and frame-count gates.

## Non-goals

- Flashing, copying, or activating DTBs and kernel modules in this slice.
- Claiming that an already-deployed target contains a PR artifact without a
  verified artifact-to-target binding.
- Running fork PR code or PR-head scripts on the self-hosted runner.
- Automatically promoting a measurement to the committed baseline.
- Replacing the release-plan engine, web dashboard, Docker runner, or every
  legacy standalone hardware runner.
- Turning the advisory board lease into a security boundary.

## Approaches Considered

### 1. Split trusted measurement and publication workflows (selected)

A self-hosted workflow runs trusted default-branch measurement code with a
read-only token. A separate GitHub-hosted `workflow_run` publisher validates
the artifact's repository, run, PR, and HEAD binding before upserting a PR
comment. This contains write permission and prevents PR-head workflow code
from publishing fabricated evidence directly.

### 2. Extend the existing hardware workflow as one monolithic job

This would require fewer workflow files, but the board-facing job would need
PR write permission. It would also mix measurement, trust validation, and
comment formatting, making fail-closed review substantially harder.

### 3. Install a persistent board-side or host-side daemon

A daemon could queue work continuously, but it would create a second scheduler
beside GitHub Actions and Project Control, require service lifecycle and secret
management, and expand the first slice well beyond the existing operational
model.

## Scope and Phase Boundary

Phase one measures the board as it is already deployed. It records target file
hashes and versions from the committed baseline and fails on an identity
mismatch, but performs no copy operation. Its PR comment is explicitly labeled
`predeployed measurement`.

Phase two may implement a `DeploymentAdapter` that accepts an immutable
manifest, copies to a staging path, verifies source and staged SHA256 values,
activates the artifact, reboots, verifies the active target SHA256, and rolls
back on failure. The phase-one evidence model already reserves a `deployment`
section so adding that adapter does not change consumers. Phase-two work needs
its own reviewed design amendment because flashing and rollback introduce
additional destructive-action policy.

## Architecture

```text
same-repository PR label / trusted manual dispatch
                         |
                         v
          hw-evidence-measure.yml (self-hosted)
            | trusted default-branch gate code
            | explicit read-only permissions
            v
       scripts/with_pim_board.sh --for 3h --purpose ...
            | recover unfinished transaction first
            | verify deployed identity
            | execute producers through adapters
            | validate measurements against baseline
            v
       hw-results/<full-pr-head-sha>.{json,md}
                         |
                         v
         hw-evidence-publish.yml (GitHub-hosted)
            | workflow_run identity validation
            | current PR HEAD validation
            v
           one marker-based PR evidence comment
```

The proposed Python subsystem is a small `hw_gate` package:

- `hw_gate.cli` resolves the run identity, loads the matrix and baseline,
  invokes adapters, computes the overall verdict, and writes artifacts.
- `hw_gate.evidence` owns schema construction and structural validation.
- `hw_gate.rules` evaluates exact, range, and relative comparisons.
- `hw_gate.transaction` strictly wraps `SetupManager` for snapshot, persistent
  recovery journal, apply/reboot, restore/reboot, and hash verification.
- `hw_gate.diagnostics` performs allowlisted, read-only failure collection.
- `hw_gate.adapters` contains shape translators for `bps_quick` and
  `mixed_combo`; adapters do not own threshold policy.
- Measurement logic and SSH collection remain in `checks/` as `BaseCheck`
  subclasses or existing producers. New remote commands use
  `SshClient.run()`; no new direct `subprocess` SSH path is introduced.

Legacy producers may retain their external JSON shape while being adapted.
Where a producer must change to satisfy restoration or SSH requirements, it is
refactored behind the same adapter rather than teaching the core two formats.

Before attempting the lease, the trusted launcher writes a run envelope that
contains only repository, PR, HEAD, workflow, and baseline identity. If local
preflight fails or the lease returns exit 4 without starting the child, a
trusted local finalizer converts that envelope into an ERROR or BUSY evidence
document. BUSY is the sole valid zero-gate terminal document; it cannot be
evaluated as PASS. This keeps the always-upload contract true even when the
board-facing process never starts.

## Workflow and Trust Boundaries

### Measurement workflow

`.github/workflows/hw-evidence-measure.yml` supports:

- `pull_request_target` on `labeled` and `synchronize`, restricted to a
  same-repository PR carrying `needs-hw-verify`; and
- `workflow_dispatch` from the repository default branch with an explicit PR
  number.

The workflow definition, gate core, baseline, and adapters are checked out
from the trusted default-branch SHA. The PR head is resolved through the API
and used only as an evidence binding; it is not checked out or executed. The
workflow has explicit `contents: read` permission, receives no repository or
board secret, and never calls the PR comment API. Official actions are pinned
to immutable commit SHAs.

GitHub `concurrency: pim-target-lock` remains as a cheap CI-to-CI serializer.
Cross-process exclusion is still authoritative at
`scripts/with_pim_board.sh`. The workflow performs local tool checks before
the lease, then acquires one lease before the first target probe and retains it
through diagnostics and configuration restoration. It never calls `board
wait`, retries acquisition, invokes `jhw-control board acquire` directly, or
converts exit 4 to success.

### Publication workflow

`.github/workflows/hw-evidence-publish.yml` runs on GitHub-hosted infrastructure
for completed measurement runs. It has `actions: read`, `contents: read`, and
`pull-requests: write`, checks out only default-branch publisher code, and
downloads the named artifact from the triggering run.

Before publishing, it verifies all of the following against the GitHub API:

- repository and expected measurement workflow identity;
- workflow run ID and attempt;
- same-repository PR number and full head SHA;
- current PR head still equals the measured head;
- evidence schema, size limit, and internal run binding; and
- recomputed overall verdict equals the producer's declared verdict.

The publisher upserts one comment containing a stable HTML marker. A stale
result replaces any earlier PASS presentation with `STALE`. If trusted
workflow metadata establishes the PR but its artifact is malformed, the
publisher may report ERROR to that trusted PR. If the run-to-PR binding itself
cannot be established, it fails without commenting; it never trusts an
artifact-supplied PR number as the destination. It renders values from
validated JSON and does not execute strings contained in the artifact.

## Canonical Evidence Contract

The phase-one JSON has schema version 1 and the following logical structure:

```json
{
  "schema_version": 1,
  "run": {
    "repository": "jhw7500/pim-check",
    "pr_number": 115,
    "pr_head_sha": "<40 lowercase hex characters>",
    "workflow_run_id": 123,
    "workflow_run_attempt": 1,
    "started_at": "<UTC timestamp>",
    "finished_at": "<UTC timestamp>"
  },
  "board": {
    "id": "pim",
    "target_host": "192.168.0.5",
    "lease_session": "<session>",
    "identity": []
  },
  "baseline": {
    "path": "baselines/hw-baseline.json",
    "sha256": "<sha256>",
    "source_commit": "<full commit sha>"
  },
  "deployment": {
    "mode": "predeployed",
    "verified": false,
    "artifacts": []
  },
  "gates": [],
  "diagnostics": [],
  "overall_verdict": "PASS"
}
```

Each gate contains:

- stable gate ID, adapter ID and adapter schema version;
- process exit code and raw-output artifact SHA256;
- `preconditions`, including expected and read-back values;
- one or more `metrics`, each with stable ID, numeric value, unit, baseline
  value, rule, computed delta, and metric verdict;
- setup and restoration evidence, including before/after configuration hashes;
  and
- a gate verdict and bounded diagnostic references.

Allowed rule kinds are:

- `exact`: observed value equals the committed expected value;
- `range`: observed value is within committed inclusive bounds; and
- `relative`: observed value stays within a committed percentage or absolute
  delta from a committed reference.

The evaluator fails closed on zero metrics, duplicate metric IDs, missing or
new metrics, missing baseline entries, NaN or infinity, unit mismatch,
malformed raw data, adapter errors, or invalid timestamps. A successful runner
exit is recorded but cannot change any metric verdict. Overall PASS requires
every gate, precondition, identity check, and restoration check to pass.

## Baseline Contract

`baselines/hw-baseline.json` is committed and reviewable. It contains:

- schema and baseline version;
- target identity claims, including allowlisted target path, expected SHA256,
  module version or other stable identifier;
- gate and metric IDs with units and comparison rules; and
- comparability context that must match before values are compared.

A missing baseline never becomes PASS. The run emits a separate candidate
artifact and terminates with ERROR; a human reviews and commits the candidate
in a later change. Neither workflow promotes or rewrites the baseline.

Old BPS observations collected with nonzero QP bounds or uncontrolled encoder
state are not eligible baseline sources. Thresholds are calibrated only from
the controlled configuration below. The design does not carry forward the
legacy 25%/10% tolerances without controlled-board evidence.

The initial BPS baseline uses three independent, fully restored runs per
setpoint. A candidate is eligible only when every sample is within 10% of its
configured target and the maximum sample-to-median deviation is at most 5%.
The committed reference is the median `actual_bps`; subsequent runs must stay
within 5% of that reference as well as within 10% of the configured target.
If any setpoint cannot satisfy the calibration criteria, baseline bootstrap
fails and that setpoint remains ungated rather than receiving a wider ad hoc
tolerance. The human-reviewed baseline records all source run IDs and samples.

## BPS Measurement Transaction

The BPS adapter uses `multi_1ch_0_720p` as its deterministic fixture: 1280x720,
30 fps, MP4, ch0 only, capture disabled, and the profile's explicit camera and
exposure settings. It evaluates 1024, 2048, 4096, and 8192 kbps. Each setpoint
is an isolated setup/test/teardown cycle so rate-control warm state from one
setpoint cannot become the next setpoint's hidden precondition.

Before mutation, the transaction:

1. confirms there is no unrecovered transaction journal;
2. snapshots the full edgeconf through `SetupManager`;
3. writes a mode-600, target-persistent recovery copy plus its SHA256 under
   `/root/shared_v/pim-check-recovery/<run-id>/`; and
4. creates the normal config-guard backup.

Snapshot or journal failure aborts before mutation. Unlike the general
`SetupManager.run_setup()` warning path, the hardware gate is strict because a
measurement job must not leave an unrestorable board.

For each setpoint the fixture applies and later reads back at least:

- `.VHL_CAM.enc = "h265"`;
- `.VHL_CAM.i2c2.ch0.bps = [target, target]`;
- `.VHL_CAM.i2c2.ch0.qp_min = [0, 0]`;
- `.VHL_CAM.i2c2.ch0.qp_max = [0, 0]`;
- `.VHL_CAM.i2c2.ch0.quant = [-1, -1]`;
- `.VHL_CAM.i2c2.ch0.profile = [0, 0]`; and
- the fixture's resolution, fps, channel-enable, recording-time, capture,
  exposure, and muxer fields.

QP zero removes explicit QP bounds; it is the empirically confirmed condition
that reduced target-BPS error. `quant = -1` selects automatic initial QP. It is
controlled to remove configuration drift, not claimed as the demonstrated BPS
fix. Any QP or quant read-back mismatch is ERROR and invalidates the bitrate
sample.

The required lifecycle is:

```text
backup/snapshot -> apply -> reboot -> readiness/read-back -> record -> ffprobe
                -> restore exact snapshot -> reboot -> hash/read-back verify
```

The adapter records `actual_bps` and derives absolute target error. PASS
requires both the committed target-accuracy rule and the committed
baseline-relative rule. The generated file must be new for the current boot
and setpoint, finalized, above a minimum size, and probeable; otherwise no
bitrate metric is accepted.

A `finally` path performs restoration for apply, reboot, recording, parsing,
and assertion failures. Restoration failure has higher precedence than a
measurement PASS and makes the gate ERROR. The persistent journal is deleted
only after the restored full-file hash matches and post-restore reboot
readiness passes.

## Mixed-Combo Adapter

The mixed-combo adapter preserves the existing A/B/C/D register scenarios and
normalizes every observed register value as a numeric exact-comparison metric.
`all_pass`, child exit status, and test-level booleans are summaries only.
Missing ROTATION, AE, AWB, or other required read-backs produce missing-metric
ERROR rather than a partial PASS.

The campaign takes one strict full-config snapshot before its first change and
restores/reboots in a `finally` path after the last scenario or any failure.
Its raw output remains an artifact for diagnosis, but canonical evidence is
produced only by the adapter. Refactoring required remote calls uses
`SshClient.run()` and does not add another `sshpass` subprocess path.

## Failure, Recovery, and Diagnostics

Terminal states are distinct:

- `PASS`: all identity, precondition, metric, and restoration assertions pass;
- `FAIL`: valid measurements violate a committed rule;
- `ERROR`: evidence is incomplete or invalid, infrastructure fails, identity
  differs, or restoration cannot be verified;
- `BUSY`: the board lease returns exit 4 before a child starts; and
- `STALE`: the PR head changed after measurement.

All states except PASS fail the measurement check. ERROR takes precedence over
FAIL when cleanup or evidence integrity is uncertain.

On FAIL or ERROR, the core collects only allowlisted read-only diagnostics
while the lease is held: bounded dmesg excerpts, module version/SHA/CRC,
selected edgeconf read-backs, process status, and bounded raw-output tails.
Credentials, complete edgeconf content, SSH command secrets, and arbitrary
files are excluded from artifacts.

Normal exceptions and SIGTERM/SIGHUP enter teardown. The existing deadline
supervisor supplies TERM, teardown grace, KILL, process-group reaping, and
eventual lease release. SIGKILL cannot execute process cleanup; the persistent
transaction journal therefore remains dirty. The next lease holder must
restore and verify that journal before probing or mutating the target. If
recovery fails, it emits ERROR and stops. This provides eventual state recovery
without pretending that an uninterruptible kill can run a trap.

## PR Evidence Comment

The publisher renders one compact block containing:

- verdict, phase-one `predeployed measurement` scope, PR HEAD, target identity,
  run link, and baseline SHA;
- per-gate metric, observed value, unit, baseline/rule, delta, and verdict;
- setup preconditions and restoration status; and
- bounded diagnostic summaries for non-PASS outcomes.

The comment never says that a PR artifact was deployed while
`deployment.mode` is `predeployed`. A future deployment adapter may set
`deployment.verified=true` only after source, staged, and active target hashes
all agree.

## Testing Strategy

Tests follow the repository's pytest and mocked-SSH conventions.

### Unit and golden tests

- Canonical schema accepts a complete document and rejects zero metrics,
  duplicates, missing/new metrics, NaN/infinity, bad units, malformed adapter
  output, and inconsistent overall verdicts.
- Exact, range, and relative rules cover both boundaries and wrong-direction
  deltas.
- Golden legacy BPS and mixed-combo outputs normalize to stable metric IDs.
- BPS tests assert QP `[0,0]`, quant `[-1,-1]`, profile `[0,0]`, and all fixture
  read-backs before a bitrate value can be accepted.
- Snapshot failure prevents mutation; apply, reboot, measure, parse, and
  assertion exceptions all enter restoration; restoration mismatch overrides
  PASS.
- A dirty recovery journal blocks measurement until verified recovery.

### Integration and workflow-contract tests

- Existing board-reservation integration coverage remains authoritative for
  busy exit 4, child exit preservation, TERM/SIGHUP handling, deadline cleanup,
  and process-tree reaping.
- New tests verify the workflow trigger restrictions, explicit permissions,
  trusted checkout, one lease wrapper, always-upload behavior, and the absence
  of PR write permission in measurement.
- Publisher tests reject the wrong repository, workflow, run, attempt, PR,
  HEAD, oversized artifact, or producer/publisher verdict disagreement.
- Comment rendering tests prove marker upsert and replacement of stale PASS.
- A static adapter contract test rejects an adapter that declares no numeric
  metric assertions, even when its fixture exits zero.

### Controlled hardware acceptance

Hardware acceptance is run only through an explicit board lease after the
implementation is locally green:

1. run the complete predeployed matrix and retain JSON/Markdown artifacts;
2. terminate a run through the supported signal path and verify lease release,
   restoration, and the next-run recovery preflight;
3. compare a real target hash with an intentionally wrong test manifest and
   observe identity ERROR without modifying the target file;
4. replay real measured evidence against a test-only baseline tightened beyond
   5% and observe regression FAIL;
5. verify the PR publisher creates and then upserts one evidence comment; and
6. review every gate declaration to confirm that no exit-only PASS path exists.

The wrong manifest and tightened baseline are test inputs and are never
promoted. The target is restored and its hash is verified before releasing the
lease.

## Bootstrap and Rollout

GitHub does not run a newly introduced `pull_request_target` workflow as
trusted default-branch code before that workflow has merged. The implementation
PR therefore uses mocked workflow-contract tests plus a manually invoked,
leased phase-one measurement for bootstrap evidence. It must not label that
manual result as an automated trusted PR gate.

After merge, a same-repository follow-up test PR exercises the default-branch
measurement workflow and publisher end to end, including marker upsert and
stale-HEAD replacement. Issue #115's phase-one milestone is not closed until
that post-merge path and controlled hardware acceptance both pass. Later PRs
then use the label-triggered gate normally.

## Acceptance Criteria

The first vertical slice is complete when:

- the measurement workflow holds one board lease and exposes busy as exit 4;
- BPS and mixed-combo adapters emit schema-valid numeric evidence;
- the BPS matrix enforces QP zero, quant auto, deterministic fixture state, and
  verified reboot-based restoration;
- committed baseline and target-identity mismatches fail closed;
- an exit-zero/no-metric fixture cannot pass;
- partial failure evidence is always uploaded;
- the publisher validates current HEAD and upserts one scoped PR comment;
- forced termination and dirty-journal recovery paths are verified;
- mocked unit/integration tests, full pytest, and ruff pass; and
- controlled hardware acceptance demonstrates SHA mismatch and greater-than-5%
  regression detection.

Automatic artifact deployment remains explicitly incomplete after these
criteria and requires the phase-two design amendment described above.
