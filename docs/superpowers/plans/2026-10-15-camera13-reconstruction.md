# Camera13 reconstruction (2026-10-15)

## Source preservation
- Original branch: feat/camera-hard-reset @ 60fcce1 (11 commits, untouched).
- Backup ref: backup/camera-hard-reset-11-20261015 @ 60fcce1.
- New branch: feat/camera13-recovery-integration at e8e0e95 (latest main).
- Existing main working directory and its execute-bit changes remain unchanged.
- The complete 11-commit cumulative patch was applied to this worktree
  and then adapted to the camera13 owner-serialized recovery contract.

## Logical changes to commit, in order (five reviewable units)
1. Reset policy and plan wiring: da9169e, 8c93402.
   Includes pim_check.py, plan.py and their CLI/plan tests.
2. Camera13 execution contract: replace deprecated call from 16cc780,
   keep session-anchor correction from 55d8e96.
   setup.py and its focused tests; no reboot on 69/70/75/124 or ambiguous result.
3. Recording session checks: d4cae25, 60fcce1.
   Includes 16 channel profiles and anchor/recording tests.
4. Acceptance contracts: 0677969, f164f0e.
   Includes camera_reset_postcheck, hard_reset_acceptance plan and tests.
5. Package ABI and provenance: d1678ba, 5e80460, 98c824f.
   MAX9296 module 2.12, CHANGELOG, AGENTS and historical design docs.

All 41 changed files were reconstructed as five non-overlapping, human-approved
logical Git commits. The original 11 commits and their backup ref remain intact.

## Behavior and verification caveats
- Changed source config: use cam-recoveryctl apply-config --wait 300;
  the daemon decides hardware vs consumer actions from source diff.
- Explicit hard reset without source changes:
  cam-recoveryctl request camera_hard_reset --wait 300.
- Both require a single matching CAM_RECOVERY_RESULT and a CLI return sentinel.
- Wait rc 124 does not cancel pending/active recovery. NEVER start an
  out-of-band reboot on timeout/BUSY/rejected/ambiguous result.
- Note: SshClient.run returns None for nonzero rc, so caller wraps the
  remote command with a separate CLI exit-status line.
- Board acceptance must check owner invocation continuity, absence of
  fallback in persistent history, MAX9296 loaded module v2.12, and
  transitions after the 2026-10-14 camera13 install.
- A real board lease is mandatory for mutation and hardware testing.
- Git commit, push, PR, merge and real board test have NOT been run here.

## Verified candidate commit partition (2026-10-15)

The following five whole-file groups were committed with separate host approvals. Each staged group passed its focused unit tests.

1. **01-owner-recovery** (4 files); focused tests: `tests/test_setup_hard_reset.py`, `tests/test_setup.py`.
2. **02-cli-plan** (6 files); focused tests: `tests/test_cli.py`, `tests/test_plan_load.py`, `tests/test_plan_campaign_restore.py`, `tests/test_plan_execute.py`.
3. **03-session-anchor** (18 files); focused tests: `tests/test_cases_recording_session_anchor.py`, `tests/test_stabilize_ready.py`.
4. **04-acceptance** (7 files); focused tests: `tests/test_cases_camera_reset_postcheck.py`, `tests/test_plan_resolve.py`.
5. **05-abi-docs** (6 files); focused tests: `tests/test_cases_bps_ch3_transition_plan.py`, `tests/test_setup_hard_reset.py`, `tests/test_integration_teardown_recovery.py`.

Original 11 commits preserved; board verification and independent code review are separate gates.

## Exact per-commit file selection

### 1. camera13 owner-managed recovery

- `setup.py`
- `tests/test_setup.py`
- `tests/test_setup_hard_reset.py`
- `profiles/cases/fault_gstapp_crash.yaml`

### 2. reset mode and plan/CLI

- `pim_check.py`
- `plan.py`
- `tests/test_cli.py`
- `tests/test_plan_load.py`
- `tests/test_plan_campaign_restore.py`
- `tests/test_plan_execute.py`

### 3. recording session guards

- `profiles/cases/multi_1ch_0_720p.yaml`
- `profiles/cases/multi_1ch_0_fhd.yaml`
- `profiles/cases/multi_1ch_3_720p.yaml`
- `profiles/cases/multi_1ch_3_fhd.yaml`
- `profiles/cases/multi_2ch_01_720p.yaml`
- `profiles/cases/multi_2ch_01_fhd.yaml`
- `profiles/cases/multi_2ch_03_720p.yaml`
- `profiles/cases/multi_2ch_03_fhd.yaml`
- `profiles/cases/multi_2ch_12_720p.yaml`
- `profiles/cases/multi_2ch_12_fhd.yaml`
- `profiles/cases/multi_3ch_012_720p.yaml`
- `profiles/cases/multi_3ch_012_fhd.yaml`
- `profiles/cases/multi_3ch_123_720p.yaml`
- `profiles/cases/multi_3ch_123_fhd.yaml`
- `profiles/cases/multi_4ch_720p.yaml`
- `profiles/cases/multi_4ch_fhd.yaml`
- `tests/test_cases_recording_session_anchor.py`
- `tests/test_stabilize_ready.py`

### 4. hardware acceptance plan

- `AGENTS.md`
- `profiles/plans/AGENTS.md`
- `profiles/plans/_template.yaml`
- `profiles/cases/camera_reset_postcheck.yaml`
- `profiles/plans/hard_reset_acceptance.yaml`
- `tests/test_cases_camera_reset_postcheck.py`
- `tests/test_plan_resolve.py`

### 5. driver ABI and historical documentation

- `profiles/base.yaml`
- `tests/test_cases_bps_ch3_transition_plan.py`
- `CHANGELOG.md`
- `docs/superpowers/plans/2026-09-01-camera-hard-reset-strategy.md`
- `docs/superpowers/specs/2026-09-01-camera-hard-reset-strategy-design.md`
- `docs/superpowers/plans/2026-10-15-camera13-reconstruction.md`

## Commit approval and review gates

Use the dedicated MCP Git workflow against project alias
pim-check-camera13, NOT pim-check (which points to the original dirty main
worktree). The alias was registered in projects.json and the tunnel was
restarted. ChatGPT plugin tool schemas must be refreshed to expose the new
project in the prepare_git_operation project's enum.

For each group, construct a Change Evidence Contract v1 message with headings:
Contract version, Why, Changes, Validation, References; include URLs to
https://github.com/jhw7500/pim-check/issues/160 and
https://github.com/jhw7500/pim-package-jhw/issues/61 as applicable.
Call prepare_git_operation with only that group's explicit files, complete
the returned approval_command in a human-controlled host terminal, then call
execute_git_operation after confirming approval. Each operation requires its
own one-use receipt. Do not stage all files or commit directly through the
generic host command runner, which would bypass the Git approval workflow.

Verified on the cumulative temporary worktree from main e8e0e95:
- stage 1: 44 passed, 2 subtests passed
- stage 2: 110 passed
- stage 3: 91 passed
- stage 4: 14 passed
- stage 5: 51 passed
- all stages: 2517 passed, 65 subtests passed; diff --check clean

Five approved Git commits were created locally. No Git push, PR, merge
or camera-board mutation was performed; board-loaded MAX9296 and hardware
acceptance remain unverified.

## Completed reconstruction commits

1. c7096b0 - feat(camera): camera13 owner-managed recovery
2. 4428e33 - feat(plan): CLI/plan reset policy
3. 1e51987 - fix(recording): strict same-boot session checks
4. d12225f - test(camera): hard reset acceptance profiles
5. 4b6c109 - chore(camera): MAX9296 v2.12 and provenance

Final committed-HEAD validation (2026-10-15): 2517 pytest tests passed
plus 65 subtests, Ruff passed, git diff --check passed, clean worktree.
The original feature branch and backup both still point to 60fcce1.

## Acceptance hardening follow-up (uncommitted)

The initial acceptance plan verified user-level checks but not an actual
camera_hard_reset action or owner invocation continuity. Its ORD/VCM case
(720p_2ch) was already present; the missing element was the read-only
postcheck at the correct point in the case sequence.

The proposed follow-up puts camera_reset_postcheck between the camera-only
4ch720p -> 1chFHD transitions and the ORD/VCM reboot-downgrade case. Each
camera-only setup opts into require_hard_reset. In this strict mode:

- Already-matching configuration must fail, not pass on stale recording.
- Take immutable cam-operate owner snapshots before and after recovery.
- Verify exact request-id history contains a SUCCEEDED camera_hard_reset
  action and no reboot_fallback, not merely a successful apply_config.
- Fail closed on missing, stale, invalid or conflicting history. Do not
  submit a second reset when the first action may still be running.
- On an ambiguous result, stop plan retries and subsequent cases; skip
  automatic teardown/restore/reboot even if source configuration was changed.
  Report that manual inspection is required before any further recovery.
- Store a token-free owner/request marker for the read-only postcheck.
- Require ACTIVE owner, no pending/active request, present video nodes,
  loaded camera modules, loaded MAX9296 version 2.12 and fresh history for
  the same owner.
- Place the 720p_2ch case with existing ord_vcm_changes AFTER this check.
  It retains the explicit reboot downgrade and does NOT need strict
  same-invocation assertions because reboot intentionally changes owner.

These follow-up source changes are not part of the five committed SHAs.
They require isolated tests, independent review, a new approved Git commit,
and an exclusive board lease before real device acceptance.
