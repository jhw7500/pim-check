# PIM Board Lock Integration Design

**Status:** Approved  
**Date:** 2026-08-24  
**Task:** `tsk-01a03249-be8c-7759-bd5e-f2fa18918799`  
**Issue:** [#108](https://github.com/jhw7500/pim-check/issues/108)

## Context

The hardware verification workflows use a shared GitHub Actions `concurrency`
group, but that group only serializes GitHub jobs. Local invocations and the
long-running automation scripts can still use the same PIM target while CI is
changing `edgeconf`, rebooting the device, testing it, and restoring it.

That overlap can invalidate both runs and, more importantly, cause one run to
restore configuration over another run's active test state. The existing
Project Control board registry already provides an advisory, cross-process
exclusive lease for board `pim`; pim-check needs to adopt that lease at every
CI and local plan entry point enumerated in issue #108.

The self-hosted runner adds two deployment constraints:

- it runs as `jhw`, but its service `PATH` does not contain
  `$HOME/.local/bin`;
- `jhw-control` needs the host configuration in
  `$HOME/.config/jhw-control/control.env`.

## Goals

- Serialize CI and the issue-scoped local plan/automation runs through the same
  exclusive `pim` board lease.
- Fail a CI run immediately when the board is busy; do not wait, skip, or turn
  the conflict into success.
- Preserve the wrapped command's stdout, stderr, signals, and exit status.
- Give long-running local automation a bounded lease covering its intended
  execution window.
- Make the safe invocation path obvious and testable without coupling the
  portable Python application to one developer-machine service.

## Non-goals

- Turning the advisory board registry into a security boundary.
- Preventing a human or non-Claude automation from deliberately bypassing the
  wrapper.
- Changing Project Control's lock, wait, reservation, or lease semantics.
- Locking read-only CI preparation such as dependency installation,
  reachability probes, artifact upload, or summary rendering.
- Retrofitting the legacy web dashboard, Docker runner, or non-plan CLI modes;
  those need a separate per-run lease design because wrapping a persistent
  server would reserve the board while it is idle.

## Approaches Considered

### 1. Common repository wrapper plus entry-point guards (selected)

A single shell wrapper owns host configuration discovery and the
`jhw-control board with` contract. CI workflows and long-running local scripts
enter through it, while a Claude `PreToolUse` hook warns and blocks known direct
high-risk commands.

This centralizes a host-specific integration, keeps lease behavior consistent,
and lets all callers share the same tests.

### 2. Inline `jhw-control` in every workflow and automation script

This has fewer new files, but duplicates configuration loading, session naming,
lease selection, and exit handling. Those copies would be likely to drift as
new hardware paths are added.

### 3. Acquire the lock inside `pim_check.py`

This would protect many direct CLI calls, but it would make the Python package
depend on a host-local service, complicate Windows and standalone use, and
still miss the standalone `run_*verify.py` programs unless each gained separate
integration.

## Architecture

The canonical boundary is a new `scripts/with_pim_board.sh` wrapper:

```text
workflow / automation / manual caller
                  |
                  v
       scripts/with_pim_board.sh
          | load trusted host config
          | resolve binary and session identity
          | acquire exclusive, bounded lease (fail fast)
          v
      jhw-control board with pim --mode exclusive ... -- command
                                                        |
                                                        v
                                                board-mutating run
```

The wrapper delegates acquisition and cleanup to `jhw-control board with`.
It must not call `board wait`. A busy board therefore retains the native
non-zero `BOARD_BUSY` result, which immediately fails CI.

The wrapper executes the child with `PIM_BOARD_LOCK_HELD=1`. If a supported
entry point is already running under that marker, a nested wrapper invocation
executes its child directly instead of trying to acquire the same exclusive
lease twice.

GitHub Actions `concurrency: pim-target-lock` remains in place. It cheaply
serializes CI jobs before they reach the cross-process board lock, while the
board lock supplies the missing coordination with local sessions.

## Wrapper Contract

The command-line interface is:

```bash
scripts/with_pim_board.sh \
  (--for DURATION | --until TIMESTAMP) \
  --purpose TEXT \
  [--long-lease true] \
  -- COMMAND [ARGUMENT ...]
```

Required behavior:

1. Source `${JHW_CONTROL_ENV:-$HOME/.config/jhw-control/control.env}` without
   printing its contents.
2. Invoke `${JHW_CONTROL_BIN:-$HOME/.local/bin/jhw-control}` so the self-hosted
   runner does not depend on its service `PATH`.
3. Use `${PIM_BOARD_ID:-pim}` in exclusive mode.
4. Use an explicit `PIM_BOARD_SESSION` when provided; otherwise derive a
   bounded, diagnosable identity from GitHub run metadata, the current Codex
   session, or a local user/process fallback, in that order.
5. Require exactly one lease boundary (`--for` or `--until`), a non-empty
   purpose, and a child command.
6. Use `exec` and preserve the board command's exit status. In particular,
   acquisition failure remains failure and child exit status `2` remains
   available to the release-plan workflow's existing result policy.
7. Produce a clear non-zero error for missing configuration, missing binary,
   invalid arguments, or an out-of-range long lease.

The wrapper owns no retry loop. Signal-safe release remains the responsibility
of `jhw-control board with`, which already releases on normal exit, `SIGINT`,
and `SIGTERM`.

## CI Integration

Only the board-mutating execution step is wrapped:

| Workflow | Wrapped command | Lease |
| --- | --- | --- |
| `hw-verify.yml` | `run_mixed_combo_verify.py` | 30 minutes |
| `hw-verify-comprehensive.yml` | `run_comprehensive_verify.py` | 3 hours |
| `hw-verify-plan.yml` | selected plan/compatibility runner | 12 hours |

The release-plan workflow keeps its current interpretation of child exit codes:
`0` and `2` are accepted results, while other child failures and all board
acquisition failures fail the step. No workflow uses `|| true`, a wait command,
or a success-on-busy branch around the wrapper.

Purpose strings include the workflow and GitHub run identity so `board status`
shows why the board is occupied.

## Local Automation Integration

Each long-running script self-reexecutes through the wrapper before creating
run state or issuing any board command:

- `auto_chain.sh` uses a bounded 24-hour long lease;
- `auto_overnight.sh` uses its computed next-morning deadline;
- `auto_weekend.sh` uses its computed Monday-morning deadline.

The overnight and weekend scripts pass `--until` rather than estimating a
duration twice. Long leases explicitly opt in with `--long-lease true`.
Project Control's 72-hour ceiling remains authoritative; an invocation whose
computed deadline exceeds it fails clearly instead of silently running with an
insufficient lease. The intended Friday-to-Monday weekend window fits within
that ceiling.

The long-lease deadline supervisor starts cleanup 31 minutes before lease
expiry. It gives the child process group 30 minutes after `SIGTERM` to finish
teardown, covering both 600-second boot polling windows around one recovery
attempt plus restore and stabilization work. A final 60 seconds remains to
kill and reap a stuck process group before the lease expires.

`auto_chain.sh` may retain its existing process check after acquiring the
lease as migration defense against an already-running legacy, unwrapped
`pim_check.py`. It is not a lock substitute and does not turn a board-lock
conflict into a wait: a compliant existing holder still causes immediate
acquisition failure.

Manual `pim_check.py --plan` and named standalone hardware-runner commands use
the same wrapper. Repository agent guidance and the existing README command
examples will identify it as the canonical entry point instead of continuing
to advertise direct plan execution.

## Claude Command Guard

A committed `.claude/settings.json` installs a `PreToolUse` hook for Bash,
following the [Claude Code hooks contract](https://code.claude.com/docs/en/hooks).
A small repository script reads the hook JSON from stdin and rejects known
direct high-risk commands, including release-plan and standalone hardware
verification entry points, unless they use the common wrapper. The self-wrapping
`auto_*` scripts remain valid entry points.

The hook exits with status `2` and writes a remediation message to stderr so
Claude blocks the tool call before execution. Unrelated shell commands pass
unchanged.

Before classifying a non-Python command as unrelated, the guard parses GNU
`env` short-option clusters (including split-string operands), unwraps `exec`,
`timeout`, and `nohup` command operands, and reparses command strings passed to
`bash`, `dash`, `sh`, or `zsh` with `-c`. Unknown or malformed launcher options
fail closed. Launcher inspection is recursive and fails closed after a bounded
nesting depth; a recognized board wrapper ends inspection because that child
already executes under the lease.

Shell grouping tokens (`()`, `{}`) are command boundaries, and command-bearing
control prefixes such as `if`, `then`, `while`, and `do` are reduced before the
inner command is classified. Compound forms that cannot be reduced without a
full shell parser (`for`, `case`, function declarations, `coproc`, and `time`)
fail closed and require a simpler invocation through the canonical wrapper.

This hook is defense in depth, not enforcement against arbitrary shell
composition or non-Claude callers. The actual coordination mechanism is the
shared advisory board lease.

## Failure Semantics

| Condition | Result |
| --- | --- |
| Board already held | Immediate non-zero `BOARD_BUSY`; CI fails |
| Host config or binary unavailable | Clear wrapper error before child starts |
| Child exits non-zero | Same exit status propagates through the wrapper |
| Child receives `SIGINT`/`SIGTERM` | `board with` releases its lease |
| Wrapper is killed with `SIGKILL` or selected by OOM | Project Control v1 cannot guarantee child termination; treat surviving-child evidence as an incident before another board run |
| Nested supported invocation | Existing lease marker is reused; no deadlock |
| Requested lease exceeds policy | Invocation fails; no shortened lease |

The `SIGKILL`/OOM case is an inherited Project Control v1 boundary: the dead
wrapper can be reaped while its child remains alive, briefly allowing physical
overlap. This task documents and tests all catchable termination paths but does
not claim to add the guardian/handshake protocol that would close that upstream
gap.

## Verification Strategy

Implementation follows test-driven development. Tests will cover:

- wrapper argument validation, host-config loading, absolute binary selection,
  session derivation, exclusive-mode arguments, nested invocation, and exact
  child/acquisition exit propagation using a temporary fake `jhw-control`;
- guard behavior for direct high-risk commands, wrapper-mediated commands,
  self-wrapping automation, unrelated commands, and malformed hook input;
- repository contracts proving that all three hardware workflows wrap their
  board-mutating step and that all long-running automation scripts self-wrap
  with the required lease policy;
- shell syntax with `bash -n` and the complete `pytest` suite.

Tests must not acquire the real board or require a physical target.

## Documentation and Rollout

The implementation updates agent/script guidance and the changelog together
with the wrapper, hook, workflow, and tests. Existing CI concurrency is retained
throughout rollout. No external Project Control state or `jhw-notion` source is
changed by this task.

The feature is complete when all CI and local plan/automation entry points named
in issue #108 are covered, busy-board behavior is proven fail-fast, child exit
semantics remain compatible, and the full local test suite passes without a
target board.
