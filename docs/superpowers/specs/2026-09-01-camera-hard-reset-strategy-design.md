> Historical 2026-09-01 design. Camera13 now uses cam-recoveryctl with owner-serialized apply-config and strict terminal results. The previous -s/-S wrapper and 45-second timeout are obsolete. See setup.py and CHANGELOG.md.

# Camera Hard Reset Strategy Design

**Status:** Approved
**Date:** 2026-09-01

## Context

`pim-check` currently treats a board reboot as the activation boundary for every
configuration-changing camera case. The comprehensive release plan contains 20
configuration cases and normally performs one additional reboot after restoring
the campaign baseline. The reboot path also waits 10 seconds before checking the
target and then polls SSH in 60-second increments.

The deployed target already provides
`/opt/pim/bin/cam_hard_reset.sh`. With `-s -S`, it stops
`cam-operate.service`, removes the MAX9296 and media modules, unbinds and rebinds
the built-in CSI2/ISI devices, reloads the modules, recreates video nodes, and
starts the service. Target qualification measured 32--34 seconds from removal to
CSI output, with a required 45-second external timeout. Exit code 0 means video
nodes and bindings were recovered; code 1 means reset failure; code 2 means an
unrecoverable negative module reference count that requires a board reboot.

The script does not restart the separate `ord` and `vcm` processes. Therefore a
camera hard reset is not a valid activation boundary for a case that changes
`ord_vcm_conf.json`. Four comprehensive regression cases change that file, while
the 16 multi-channel delta cases change only `edgeconf_pim.json`.

## Goals

- Use the qualified camera hard-reset path for camera-only configuration changes.
- Preserve a full board reboot whenever ORD/VCM runtime state must be reloaded.
- Fall back once to a board reboot when a requested hard reset is unavailable,
  times out, fails, or reports an unrecoverable module state.
- Prevent same-boot test sessions from matching recordings or health state from a
  previous case.
- Apply the same reset selection to setup and exact campaign restoration.
- Keep existing profiles and callers on reboot unless a plan explicitly opts in.
- Make the selected path and every fallback visible in console and board logs.

## Non-goals

- Modifying or redeploying `cam_hard_reset.sh` from this repository.
- Treating a gstApp-only or watchdog process restart as equivalent to hard reset.
- Replacing reboot for network, ORD/VCM, kernel, module-installation, or other
  non-camera state transitions.
- Changing the fixed 300-second comprehensive monitoring window.
- Retrofitting the persistent web dashboard or Docker runner with a new default.
- Hiding a failed hard reset behind an unreported successful reboot.

## Approaches Considered

### 1. Hybrid plan-selected strategy with safe reboot downgrade (selected)

A plan selects `reboot` or `camera_hard`. `camera_hard` is used only when the
applied mutation is camera-only. ORD/VCM changes directly select reboot. A failed
hard reset also selects reboot exactly once. This preserves the existing release
gate while shortening the 16 comprehensive multi-channel transitions.

### 2. Hard reset every case and restart ORD/VCM separately

This could remove four more reboots, but the repository has no authoritative
service-level restart contract for ORD/VCM. The watchdog `pkill` smoke case proves
eventual respawn, not atomic configuration reload or exact restoration. This
approach is deferred until ORD/VCM exposes a qualified reload boundary.

### 3. Reduce only the reboot polling interval

Adaptive polling is lower risk and may be considered separately, but it cannot
remove shutdown, boot, and service initialization time. It also does not exercise
the already-qualified CSI2/ISI hard-reset path. It is not part of this change.

## Configuration Contract

Plan execution gains one optional key:

```yaml
execution:
  reset_mode: reboot       # reboot | camera_hard
```

`reboot` remains the schema default. Unknown values fail plan linting before the
board is touched. A CLI `--reset-mode reboot|camera_hard` override is accepted only
for plan runs so the same acceptance plan can be exercised with both strategies.

The existing case key `setup.reboot_after` remains the boolean activation switch.
Renaming all existing profiles would create unrelated churn; when it is true, the
selected reset strategy runs after apply and after restore.

The comprehensive plan changes to `reset_mode: camera_hard` only after mocked
tests and controlled target acceptance pass. At runtime:

- edgeconf-only mutation: camera hard reset;
- any `ord_vcm_changes`: direct board reboot;
- no effective mutation: no reset, but refresh the test-session anchor;
- hard-reset failure: visible warning followed by one board reboot.

## SetupManager Design

`SetupManager` owns a single dispatcher used by both `run_setup` and
`run_teardown`:

```python
reset_and_wait(mode: str, stabilize_sec: int, *, ord_changed: bool) -> str
```

The return value is the effective path, `camera_hard` or `reboot`, for logging and
unit assertions. `ord_changed=True` bypasses the hard-reset command entirely.

The camera command is executed only through `SshClient.run()` and wraps the
target script so its exit code is returned in a parseable sentinel while the
outer shell exits successfully:

```sh
timeout 45 /opt/pim/bin/cam_hard_reset.sh -s -S
rc=$?
printf '\nPIM_HARD_RESET_RC=%s\n' "$rc"
exit 0
```

The reset command is not retried through the generic SSH retry wrapper. Repeating
a partially completed unbind/rebind sequence after an ambiguous connection error
is unsafe. A missing/malformed sentinel, missing script, timeout, SSH exception,
or any nonzero script result produces one explicit fallback reboot. Exit code 2
is never retried as a hard reset. Before fallback reboot, the same-boot anchor
preservation flag is cleared so post-reboot readiness writes a new boot anchor.

After a successful hard reset, the existing staged readiness gate runs with the
case's current timeout. It must observe the required processes, healthy and fresh
`cam_state`, AE settle where configured, and recording readiness. Script exit 0
alone is not sufficient case readiness.

## Same-Boot Session Freshness

The current session anchor writes `uptime -s`, which is identical across hard
resets. Hard-reset mode instead writes board wall-clock time and the current
`boot_id` immediately before invoking the reset. The readiness-stage anchor check
must preserve and validate that value rather than overwrite it with boot time.
This ensures custom checks select recordings and local0 sessions produced after
the current reset.

When setup is skipped because configuration already matches, camera cases still
write a new same-boot wall-clock anchor before validation. This also applies to a
retry, preventing a retry from silently reusing recordings selected by the prior
attempt's anchor. Reboot mode retains its existing boot-time anchor behavior.

The hard-reset script stops and starts `cam-operate.service`. Its success check is
followed by the existing process, `cam_state` heartbeat, AE, and recording gates.
No stale `/tmp` file is deleted blindly; freshness is established by the new
session boundary and the existing heartbeat/settle checks.

## Restoration and Failure Semantics

Campaign snapshots and exact restore behavior remain unchanged. After restoring
files, the reset dispatcher evaluates the union of paths changed during the
campaign:

- if `ord_vcm_conf.json` was touched, restoration uses reboot;
- if only `edgeconf_pim.json` was touched and the plan selected `camera_hard`,
  restoration uses hard reset;
- a restore-time hard-reset failure falls back once to reboot.

Fallback is a correctness-preserving degradation, not a PASS claim for the hard
reset itself. Console output and the board's `[PIM_CHECK]` local0 markers include
the requested mode, effective mode, reason, and parsed hard-reset return code.
The case may still pass after fallback because the original reboot activation
boundary was completed successfully.

## Plan and CLI Flow

`plan.py` validates and defaults `execution.reset_mode`. During case resolution,
the plan execution value is copied into the runtime setup dictionary without
mutating the loaded case profile. This copied setup dictionary is also retained
for final campaign restoration, so setup and restore cannot accidentally use
different strategies.

`pim_check.py --reset-mode` overrides the loaded plan's execution value before
printing or executing the plan. Supplying the flag without `--plan` produces the
same kind of visible ignored-option warning used by `--until-pass`; it never
silently changes standalone, parallel, web, or stream behavior.

## Testing Strategy

Implementation follows test-driven development.

Unit and integration tests cover:

- plan default, accepted values, and rejection of an unknown reset mode;
- CLI override and non-plan warning behavior;
- edgeconf-only hard-reset selection and exact remote command shape;
- ORD/VCM direct reboot downgrade without attempting hard reset;
- exit 0 success, exit 1, exit 2, timeout, SSH exception, missing script, and
  malformed sentinel fallback paths;
- no hard-reset retry after an ambiguous or unrecoverable result;
- reset-time anchor preservation and setup-skip anchor refresh;
- identical strategy selection during campaign restoration;
- existing reboot behavior remaining unchanged when no mode is selected.

The full mocked suite (`pytest`) and `ruff check` must pass before target use.

Controlled hardware acceptance runs only through
`scripts/with_pim_board.sh` with an explicit lease and purpose. The same bounded
acceptance plan is run first with `--reset-mode reboot` and then with
`--reset-mode camera_hard`. It includes:

- an edgeconf-only 4-channel 720p case;
- an edgeconf-only 1-channel FHD topology/resolution transition; and
- a legacy case with `ord_vcm_changes` to prove direct reboot downgrade.

Acceptance requires identical case verdicts and configuration readbacks, no
fallback for the two camera-only cases, direct reboot for the ORD/VCM case, clean
campaign restoration, active camera services, fresh recordings, and no negative
module reference count. Only then is comprehensive switched to `camera_hard` and
run through its normal 100% release gate.

## Expected Time Impact

The qualified hard reset takes about 32--34 seconds. The current reboot path has
a fixed 10-second delay plus 60-second SSH polling increments before the same
readiness gates. Sixteen comprehensive setup transitions become eligible for hard
reset; four ORD/VCM transitions and the final campaign restore remain reboots.
The fixed 300-second monitoring windows and any case retries are unchanged, so
the optimization is expected to save transition time rather than alter test
coverage or gate strength.
