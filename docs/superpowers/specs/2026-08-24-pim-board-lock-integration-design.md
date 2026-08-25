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

The wrapper executes the child with `PIM_BOARD_LOCK_HELD=1` plus the owning
`board with` PID, session, and board id. The boolean is only a hint: a nested
wrapper skips a second acquisition only when the recorded PID is an ancestor
running `jhw-control board with` and read-only `board status` reports the same
live, unexpired exclusive session. A caller-supplied marker without that active
lease evidence falls back to normal acquisition and can never directly launch
the child. Supported automation entry points use the wrapper's `--check-held`
probe instead of trusting the environment variable themselves. That probe is
strict: it also requires the automation's exact absolute deadline and purpose,
the owning `board with` command's `--long-lease true`, a covering
`granted_until` from `board status`, and a matching ancestor deadline
supervisor with the full cleanup budget. A shorter or unrelated outer lease
therefore cannot satisfy an automation self-wrap.

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

Long-running automation verifies only its own supervised lease with:

```bash
scripts/with_pim_board.sh \
  --check-held \
  --until TIMESTAMP \
  --purpose TEXT \
  --long-lease true
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
8. Reuse an active lease for a long-lease child only when the strict probe
   evidence matches; otherwise attempt normal acquisition and preserve its
   busy failure without launching the child.

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

- `auto_chain.sh` computes an absolute deadline 24 hours ahead;
- `auto_overnight.sh` uses its computed next-morning deadline;
- `auto_weekend.sh` uses its computed Monday-morning deadline.

All three scripts preserve their target epoch across self-reexec and pass the
same exact `--until`, purpose, and `--long-lease true` contract to both the
strict probe and acquisition. This avoids estimating a duration twice.
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
`env` short-option clusters (including split-string operands), unwraps
`builtin`, `exec`, `nice`, `stdbuf`, `xargs`, `flock`, `setarch`,
`start-stop-daemon`, `chroot`, `systemd-run`, `watch`, `taskset`, `chrt`, `ionice`, `script`,
`prlimit`, `setsid`, `unshare`, `sudo`, `timeout`, and `nohup` command operands,
reparses `eval` arguments as a command string, and reparses command strings
passed to `bash`, `dash`, `sh`, or `zsh` with `-c`.

For util-linux `flock`, direct command argv is classified recursively and the
`-c`/`--command` form is reparsed as a shell command string. A sole numeric file
descriptor is the documented non-launching form and remains allowed; missing,
ambiguous, or unsupported launcher forms fail closed.
For util-linux `setarch` and its installed `i386`, `linux32`, `linux64`, and
`x86_64` aliases, personality options are separated from the program argv,
which is recursively classified. Program-less forms that would start a default
shell and unknown options fail closed; terminal options remain allowed. The
separate coreutils `arch` executable is not treated as a launcher.
For dpkg `start-stop-daemon --start`, `--startas` selects the launched program
when present and otherwise `--exec` does. The selected program and arguments
after `--` are classified together. Well-formed stop, status, help/version, and
test-only forms have no launched child. Because start mode changes directory to
root by default, relative wrapper paths are never exempt; chrooted starts,
missing or conflicting commands or programs, and unknown options fail closed.
For coreutils `chroot`, options and `NEWROOT` are separated from the command
argv. Only exact `/` preserves executable identity and permits recursive child
classification; every alternate or dynamic root fails closed. Default chdir
disables relative wrapper exemption, while `--skip-chdir /` preserves the
incoming relative-path policy. A missing command would start an interactive
shell and therefore fails closed, as do unknown or empty options.
For systemd 249 `systemd-run`, documented options are separated from the
transient command argv and the child is recursively classified. Relative
wrapper exemption is preserved only for `--scope` when the incoming working
directory is still trusted; service mode and explicit working-directory
changes require the repository-absolute wrapper. Interactive `--shell`, a
missing command, remote host or machine execution, arbitrary unit properties,
and unknown or empty options fail closed because executable identity or launch
semantics cannot be established statically. Help and version modes remain
non-launching terminal forms.
Before locating that executable, the guard skips leading shell input/output,
append, clobber, bidirectional, here-document, and here-string redirections.
Attached and split targets, numeric or named file-descriptor prefixes, and
redirections interleaved with assignments or following `env` are handled alike.
Redirection targets remain data even when their basename matches a hardware
runner; a missing target or ambiguous descriptor duplication fails closed.
GNU `find` execution actions (`-exec`, `-execdir`, `-ok`, and `-okdir`) are
scanned in order and each child command is classified recursively. Escaped
semicolon terminators and exact `{}` placeholders remain data tokens during
shell segmentation. Actions containing `{}` are additionally classified with
each known board-mutating executable as a possible substitution, preventing a
matched pathname from becoming a dynamic execution target while preserving
benign data arguments and the canonical lease wrapper. Empty commands or
missing `;`/`+` terminators fail closed.
Procps-ng `watch` options are parsed before its child is classified. Direct
`--exec`/`-x` children retain their argument boundaries, while default-mode
children are rejoined and passed through shell-command classification to match
`watch`'s `sh -c` behavior. Unsupported options and missing values or children
fail closed; help/version and canonical wrapped children remain allowed.
Util-linux `taskset` options are parsed before its operands. Execution mode
skips the CPU mask or list and recursively classifies the following command,
while `--pid`/`-p` query and update forms have no child command. Unknown
options, missing operands, and extra PID-mode operands fail closed; terminal
help/version forms remain allowed.
`source` and `.` skip one optional `--` terminator before classifying the
sourced filename, so standalone hardware runners remain blocked through the
documented builtin form.
Util-linux `chrt` policy and scheduling options are parsed before its
operands. Execution mode skips the priority and recursively classifies the
following command, while `--pid`/`-p` query and update forms have no child.
Unknown options, missing option values or operands, and extra PID-mode
operands fail closed; max/help/version forms remain allowed.
Util-linux `ionice` class, classdata, and ignore options are parsed before its
operands. Execution-mode children are recursively classified, while
`--pid`/`-p`, `--pgid`/`-P`, and `--uid`/`-u` query and update forms have no
child command. Unknown options and missing values fail closed; no-argument
query and help/version forms remain allowed.
Util-linux `script` logging, timing, echo, and output options are separated
from its execution behavior. The `-c`/`--command` shell string is recursively
classified, while its optional positional operand remains an output file.
Forms without a command start an interactive shell and therefore fail closed,
as do duplicate commands, unknown options, missing values, and extra output
files; help/version and canonical wrapped command strings remain allowed.
Util-linux `prlimit` resource and output options are parsed before its command
operand, whose argv is recursively classified. PID target forms and forms
without a command have no execution child. PID forms with trailing command
tokens, duplicate PID options, unknown options, and missing or empty required
values fail closed; help/version, queries, and canonical wrapped children
remain allowed.
Unquoted Bash ANSI-C `$'...'` strings without backslash escapes are normalized
before that recursive classification. ANSI-C escape sequences and unterminated
forms fail closed instead of approximating Bash decoding; quoted or escaped
literal `$'...'` text remains unchanged.
Unknown or malformed launcher options fail closed. Launcher inspection is
recursive and fails closed after a bounded nesting depth. Only
`scripts/with_pim_board.sh`, `./scripts/with_pim_board.sh`, and the resolved
repository-absolute wrapper path end inspection; basename lookalikes fail
closed.

Shell invocations that read commands from standard input (`-s`, no script,
`-`, input redirections, or canonical fd-zero script paths) fail closed instead
of attempting to reconstruct stdin. Terminal `--help` and `--version` modes
remain allowed. A `-c` command string without trailing operands retains its
recursive classification. Any positional operand after that string fails closed
instead of approximating shell-specific `$0`, `$@`, `$*`, numbered, or indirect
expansion behavior. Positional scripts retain their inventory-based
classification.

Command-bearing tokens are also static-only where expansion could change the
guard's classification: the executable, Python script or module, shell
positional or sourced script, and every argument to `pim_check.py`/`pim-check`.
A remaining `$`, backtick, glob (`*`, `?`, `[`), or brace-expansion marker in
one of those positions fails closed instead of predicting shell expansion.
Because quote context is removed during tokenization, quoted or escaped marker
literals in those positions receive the same conservative treatment. Expansion
markers in unrelated data arguments remain allowed, as do child arguments after
the literal canonical board wrapper has established the lease boundary.
The `pim_check.py` parser currently accepts the unique argparse abbreviations
`--pl` and `--pla` for `--plan`. The guard treats their split and `=value`
forms as plan execution too, while leaving ambiguous `--p` to the CLI's normal
rejection path.

Outer-shell-active `$()` and backtick command substitutions are extracted from
the raw command with quote context intact and passed through the same bounded
classifier before a board wrapper is exempted. Single-quoted or escaped
substitutions intentionally passed to the wrapped child remain valid, while a
wrapper argument expanded before lease acquisition cannot bypass the guard. The
standalone runner inventory also includes `test_vflip_frame_compare.sh`; both
direct execution, a shell positional-script invocation, and `source`/`.` of the
runner require the lease wrapper.

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
