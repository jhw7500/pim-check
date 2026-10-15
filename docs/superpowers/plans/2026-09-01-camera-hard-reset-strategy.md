> Historical 2026-09-01 design. Camera13 now uses cam-recoveryctl with owner-serialized apply-config and strict terminal results. The previous -s/-S wrapper and 45-second timeout are obsolete. See setup.py and CHANGELOG.md.

# Camera Hard Reset Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shorten camera-only release-plan transitions with the qualified CSI2/ISI hard reset while retaining a full reboot for ORD/VCM changes and every hard-reset failure.

**Architecture:** `plan.py` owns the opt-in `reset_mode` policy and copies it into each case runtime plus campaign teardown. `SetupManager` owns one setup/restore dispatcher that selects camera hard reset only for edgeconf-only mutations, preserves a same-boot session anchor, and visibly falls back once to reboot. Existing non-plan callers and profiles keep reboot semantics.

**Tech Stack:** Python 3.9+, pytest/unittest mocks, PyYAML plan profiles, Paramiko-backed `SshClient.run()`, target Bash reset script.

**Spec:** `docs/superpowers/specs/2026-09-01-camera-hard-reset-strategy-design.md`

## Global Constraints

- Prefix every shell command with `rtk`.
- Use `SshClient.run()` for every new target command; do not add subprocess SSH.
- Keep `reboot` as the default reset mode for existing plans and non-plan paths.
- Never hard reset when `ord_vcm_changes` is non-empty; select reboot directly.
- Invoke hard reset exactly as `timeout 45 /opt/pim/bin/cam_hard_reset.sh -s -S` and never retry an ambiguous/nonzero result.
- Any missing script, timeout, malformed output, SSH exception, or nonzero reset result falls back once to reboot with visible console and local0 evidence.
- Preserve snapshot/apply/test/restore ordering and exact campaign restoration.
- Run physical target commands only through `scripts/with_pim_board.sh` with explicit duration and purpose; BUSY exit 4 remains visible with no wait/retry/`|| true`.
- Do not touch the user-owned `HANDOFF.pim-check-issues.md` in the main checkout.

---

### Task 1: Plan schema and CLI reset-mode policy

**Files:**
- Modify: `plan.py:26-49,238-260`
- Modify: `pim_check.py:39-95,190-255,611-640`
- Modify: `tests/test_plan_load.py:31-199,214-270`
- Modify: `tests/test_cli.py:1-60`

**Interfaces:**
- Produces: `plan.ALLOWED_RESET_MODES == {"reboot", "camera_hard"}`.
- Produces: `DEFAULT_EXECUTION["reset_mode"] == "reboot"`.
- Produces: CLI namespace field `reset_mode: str | None` from `--reset-mode`.
- Produces: `_run_plan()` applies a CLI reset-mode override to `plan.execution` before execution.

- [ ] **Step 1: Write failing plan-policy tests**

Add these assertions to `tests/test_plan_load.py`:

```python
def test_reset_mode_accepts_reboot_and_camera_hard(self):
    for mode in ("reboot", "camera_hard"):
        plan = {**VALID_PLAN, "execution": {"reset_mode": mode}}
        self.assertEqual(lint_plan(plan), [])

def test_reset_mode_rejects_unknown_value(self):
    plan = {**VALID_PLAN, "execution": {"reset_mode": "process_restart"}}
    errors = lint_plan(plan)
    self.assertTrue(any("reset_mode" in error for error in errors))

def test_reset_mode_defaults_to_reboot(self):
    self._write(
        "name: Smoke\n"
        "description: smoke desc\n"
        "version: 1\n"
        "cases:\n  regression: [720p_2ch]\n"
        "gate: {}\n"
    )
    self.assertEqual(load_plan(self.plan_path).execution["reset_mode"], "reboot")
```

- [ ] **Step 2: Run the plan-policy tests and verify RED**

Run:

```bash
rtk pytest tests/test_plan_load.py -q
```

Expected: FAIL because `reset_mode` has no default or enum validation.

- [ ] **Step 3: Implement the plan policy**

Add this policy to `plan.py`:

```python
ALLOWED_RESET_MODES = {"reboot", "camera_hard"}

DEFAULT_EXECUTION: dict[str, Any] = {
    # existing keys remain unchanged
    "reset_mode": "reboot",
}
```

In `lint_plan()`, validate the optional value:

```python
reset_mode = exec_cfg.get("reset_mode", "reboot")
if reset_mode not in ALLOWED_RESET_MODES:
    errors.append(
        f"execution.reset_mode: {reset_mode!r} 미지원. "
        f"허용: {sorted(ALLOWED_RESET_MODES)}"
    )
```

- [ ] **Step 4: Run the plan-policy tests and verify GREEN**

Run:

```bash
rtk pytest tests/test_plan_load.py -q
```

Expected: all tests in `tests/test_plan_load.py` PASS.

- [ ] **Step 5: Write failing CLI tests**

Add to `tests/test_cli.py`:

```python
def test_reset_mode_flag(self):
    args = pim_check.parse_args(
        ["--plan", "comprehensive", "--reset-mode", "camera_hard"]
    )
    self.assertEqual(args.reset_mode, "camera_hard")

def test_reset_mode_default_none(self):
    args = pim_check.parse_args(["--plan", "comprehensive"])
    self.assertIsNone(args.reset_mode)
```

Add a `_run_plan` test using patched `load_plan` and `execute_plan` that asserts:

```python
loaded_plan.execution["reset_mode"] == "camera_hard"
```

Add a non-plan main-flow test that calls `pim_check.main(["--list", "--reset-mode", "camera_hard"])` and asserts stdout contains both `--reset-mode` and `--plan`.

- [ ] **Step 6: Run the CLI tests and verify RED**

Run:

```bash
rtk pytest tests/test_cli.py -q
```

Expected: FAIL because argparse does not recognize `--reset-mode`.

- [ ] **Step 7: Implement the CLI override and warning**

Add to `parse_args()`:

```python
parser.add_argument(
    "--reset-mode",
    choices=("reboot", "camera_hard"),
    default=None,
    help="plan reset mode 일회성 오버라이드 (reboot | camera_hard)",
)
```

After loading the plan in `_run_plan()`:

```python
if args.reset_mode is not None:
    plan.execution["reset_mode"] = args.reset_mode
```

Before plan dispatch in `_main_run()`:

```python
if args.reset_mode and not args.plan:
    print("WARNING: --reset-mode 는 --plan 실행에만 적용됩니다 (무시됨).")
```

Include `reset_mode` and the CLI-override marker in the existing execution summary line.

- [ ] **Step 8: Run focused tests and commit**

Run:

```bash
rtk pytest tests/test_plan_load.py tests/test_cli.py -q
rtk git diff --check
rtk git add plan.py pim_check.py tests/test_plan_load.py tests/test_cli.py
rtk git commit -m "feat(plan): add reset mode policy and CLI override"
```

Expected: focused tests PASS and the commit contains only plan/CLI policy.

---

### Task 2: Hard-reset dispatcher and reboot fallback

**Files:**
- Create: `tests/test_setup_hard_reset.py`
- Modify: `setup.py:12-26,317-370,898-906,950-1049,1121-1167`

**Interfaces:**
- Produces: `HARD_RESET_PATH = "/opt/pim/bin/cam_hard_reset.sh"`.
- Produces: `HARD_RESET_TIMEOUT_SEC = 45`.
- Produces: `SetupManager.reset_and_wait(mode: str, stabilize_sec: int, *, ord_changed: bool) -> str`.
- Consumes: `setup_config["reset_mode"]`, defaulting to `"reboot"`.

- [ ] **Step 1: Write the failing dispatcher tests**

Create `tests/test_setup_hard_reset.py` with a helper that replaces readiness and reboot boundaries but exercises real dispatcher parsing:

```python
from unittest.mock import MagicMock

import pytest

from setup import SetupManager


def manager(output="reset log\nPIM_HARD_RESET_RC=0"):
    ssh = MagicMock()
    ssh.run.return_value = output
    mgr = SetupManager(ssh)
    mgr._stabilize = MagicMock()
    mgr.reboot_and_wait = MagicMock()
    mgr._local0_log = MagicMock()
    return mgr, ssh


def test_camera_hard_success_uses_qualified_command_and_stabilizes():
    mgr, ssh = manager()
    effective = mgr.reset_and_wait("camera_hard", 120, ord_changed=False)
    assert effective == "camera_hard"
    command = next(call.args[0] for call in ssh.run.call_args_list
                   if "cam_hard_reset.sh" in call.args[0])
    assert "timeout 45 /opt/pim/bin/cam_hard_reset.sh -s -S" in command
    assert ssh.run.call_args_list[0].kwargs.get("retries", 0) == 0
    mgr._stabilize.assert_called_once_with(120)
    mgr.reboot_and_wait.assert_not_called()


@pytest.mark.parametrize("output", [
    "PIM_HARD_RESET_RC=1",
    "PIM_HARD_RESET_RC=2",
    "PIM_HARD_RESET_RC=124",
    "reset output without sentinel",
    None,
])
def test_camera_hard_failure_falls_back_once_to_reboot(output):
    mgr, _ = manager(output)
    effective = mgr.reset_and_wait("camera_hard", 120, ord_changed=False)
    assert effective == "reboot"
    mgr.reboot_and_wait.assert_called_once_with(stabilize_sec=120)


def test_ord_change_uses_reboot_without_attempting_hard_reset():
    mgr, ssh = manager()
    effective = mgr.reset_and_wait("camera_hard", 120, ord_changed=True)
    assert effective == "reboot"
    assert not any("cam_hard_reset.sh" in call.args[0]
                   for call in ssh.run.call_args_list)
    mgr.reboot_and_wait.assert_called_once_with(stabilize_sec=120)
```

Add separate tests where `ssh.run` raises `RuntimeError("connection lost")`, and
where output contains two sentinel lines. The exception must cause one reboot;
multiple sentinels must be treated as malformed and cause one reboot.

- [ ] **Step 2: Run dispatcher tests and verify RED**

Run:

```bash
rtk pytest tests/test_setup_hard_reset.py -q
```

Expected: FAIL with missing `SetupManager.reset_and_wait`.

- [ ] **Step 3: Implement minimal dispatcher behavior**

Add constants and a strict sentinel parser to `setup.py`:

```python
HARD_RESET_PATH = "/opt/pim/bin/cam_hard_reset.sh"
HARD_RESET_TIMEOUT_SEC = 45
HARD_RESET_SENTINEL = "PIM_HARD_RESET_RC="

def _hard_reset_rc(output: str | None) -> int | None:
    matches = re.findall(r"^PIM_HARD_RESET_RC=(\d+)$", output or "", re.MULTILINE)
    return int(matches[0]) if len(matches) == 1 else None
```

Implement the dispatcher on `SetupManager`:

```python
def reset_and_wait(self, mode: str, stabilize_sec: int, *, ord_changed: bool) -> str:
    if mode != "camera_hard" or ord_changed:
        if ord_changed and mode == "camera_hard":
            print("Camera hard reset bypassed: ord_vcm changes require reboot")
            self._local0_log("reset DOWNGRADE camera_hard -> reboot — ord_vcm changed")
        self.reboot_and_wait(stabilize_sec=stabilize_sec)
        return "reboot"

    command = (
        f"timeout {HARD_RESET_TIMEOUT_SEC} {HARD_RESET_PATH} -s -S; "
        "rc=$?; printf '\\nPIM_HARD_RESET_RC=%s\\n' \"$rc\"; exit 0"
    )
    try:
        output = self.ssh.run(command, retries=0)
        rc = _hard_reset_rc(output)
    except Exception as exc:
        rc = None
        reason = f"{type(exc).__name__}: {exc}"
    else:
        reason = f"rc={rc}" if rc is not None else "missing/malformed sentinel"

    if rc == 0:
        print("Camera hard reset complete")
        self._local0_log("reset EFFECTIVE camera_hard — rc=0")
        self._stabilize(stabilize_sec)
        return "camera_hard"

    print(f"WARNING: camera hard reset failed ({reason}) — falling back to reboot")
    self._local0_log(f"reset FALLBACK camera_hard -> reboot — {reason}")
    self.reboot_and_wait(stabilize_sec=stabilize_sec)
    return "reboot"
```

- [ ] **Step 4: Route setup and teardown through the dispatcher**

Replace direct calls guarded by `reboot_after` in `run_setup()` and
`run_teardown()` with:

```python
reset_mode = setup_config.get("reset_mode", "reboot")
self.reset_and_wait(
    reset_mode,
    stabilize_sec,
    ord_changed=bool(ord_changes),
)
```

Keep all backup, apply, snapshot, restore, inject, and recovery ordering unchanged.

- [ ] **Step 5: Run focused setup tests and verify GREEN**

Run:

```bash
rtk pytest tests/test_setup_hard_reset.py tests/test_setup.py tests/test_setup_snapshot.py tests/test_setup_teardown_readiness.py -q
```

Expected: all focused setup tests PASS.

- [ ] **Step 6: Commit the dispatcher**

Run:

```bash
rtk git diff --check
rtk git add setup.py tests/test_setup_hard_reset.py
rtk git commit -m "feat(setup): add camera hard reset with reboot fallback"
```

---

### Task 3: Same-boot session anchor and setup-skip freshness

**Files:**
- Modify: `setup.py:48-90,317-370,712-739,950-1011`
- Modify: `tests/test_setup_hard_reset.py`
- Modify: `tests/test_stabilize_ready.py:882-936`
- Modify: `tests/test_setup.py:194-205`

**Interfaces:**
- Produces: `SetupManager._prime_same_boot_anchor() -> bool`.
- Produces: `SetupManager._preserve_session_anchor: bool` reset at each `run_setup()`.
- Consumes: the existing `SESSION_ANCHOR_PATH` and `BOOT_ID_PATH` constants.

- [ ] **Step 1: Write failing same-boot anchor tests**

Add tests that require the prime command to use current board time and boot ID:

```python
def test_prime_same_boot_anchor_writes_wall_clock_and_boot_id():
    mgr, ssh = manager("2026-09-01 12:00:00\nboot-id")
    assert mgr._prime_same_boot_anchor() is True
    command = ssh.run.call_args.args[0]
    assert "date '+%Y-%m-%d %H:%M:%S'" in command
    assert "/proc/sys/kernel/random/boot_id" in command
    assert "> /tmp/pim_check_anchor" in command

def test_hard_reset_primes_anchor_before_reset_command():
    mgr, ssh = manager()
    ssh.run.side_effect = [
        "2026-09-01 12:00:00\nboot-id",
        "reset log\nPIM_HARD_RESET_RC=0",
    ]
    mgr.reset_and_wait("camera_hard", 120, ord_changed=False)
    commands = [call.args[0] for call in ssh.run.call_args_list]
    assert "date '+%Y-%m-%d %H:%M:%S'" in commands[0]
    assert "cam_hard_reset.sh" in commands[1]
```

In `tests/test_stabilize_ready.py`, add a test that sets
`mgr._preserve_session_anchor = True`, calls `_write_session_anchor()`, and asserts
the command reads/validates the existing anchor and does not contain `uptime -s`
or overwrite redirection.

In `tests/test_setup.py`, extend the matched-config case with
`reset_mode: camera_hard` and `ready_camera_init=True`; assert one same-boot anchor
write occurs but no backup, hard reset, or reboot command occurs.

Add a fallback regression test that first primes a same-boot anchor, returns
`PIM_HARD_RESET_RC=2`, and asserts `_preserve_session_anchor is False` before the
mocked reboot boundary completes. This ensures the subsequent reboot readiness
writer creates a boot anchor instead of validating an anchor cleared by boot.

- [ ] **Step 2: Run anchor tests and verify RED**

Run:

```bash
rtk pytest tests/test_setup_hard_reset.py tests/test_stabilize_ready.py tests/test_setup.py -q
```

Expected: FAIL because same-boot priming and preserve mode do not exist.

- [ ] **Step 3: Implement anchor priming and preservation**

Initialize state in `SetupManager.__init__()` and reset it at the start of
`run_setup()`:

```python
self._preserve_session_anchor = False
```

Add the writer:

```python
def _prime_same_boot_anchor(self) -> bool:
    command = (
        f"printf '%s\\n%s\\n' \"$(date '+%Y-%m-%d %H:%M:%S')\" "
        f"\"$(cat {BOOT_ID_PATH} 2>/dev/null)\" > {SESSION_ANCHOR_PATH}; "
        f"sync; cat {SESSION_ANCHOR_PATH} 2>/dev/null"
    )
    try:
        output = self.ssh.run(command, retries=0)
    except Exception:
        return False
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if len(lines) != 2:
        return False
    self._preserve_session_anchor = True
    return True
```

For preserve mode, `_write_session_anchor()` reads the existing two lines and
verifies line 2 equals current `boot_id`; reboot mode retains the current
`uptime -s` writer. It must never move a hard-reset anchor during readiness
debounce.

- [ ] **Step 4: Prime before hard reset and on camera-hard setup skip**

At the start of the camera-hard branch in `reset_and_wait()`, require
`_prime_same_boot_anchor()`. If priming fails, report the reason and fall back to
reboot without invoking the hard-reset script.

Before every hard-reset-to-reboot fallback, set:

```python
self._preserve_session_anchor = False
```

This includes anchor-prime failure, SSH exception, missing/malformed sentinel,
and reset return codes 1, 2, and 124.

In the `edge_match and ord_match` branch of `run_setup()`, add:

```python
if ready_camera_init and setup_config.get("reset_mode", "reboot") == "camera_hard":
    if not self._prime_same_boot_anchor():
        raise RuntimeError("failed to refresh same-boot session anchor")
```

This path refreshes evidence freshness but still returns `False`, so campaign
restore remains skipped when no file changed.

- [ ] **Step 5: Run focused anchor tests and commit**

Run:

```bash
rtk pytest tests/test_setup_hard_reset.py tests/test_stabilize_ready.py tests/test_setup.py tests/test_cases_kernel_log_source.py -q
rtk git diff --check
rtk git add setup.py tests/test_setup_hard_reset.py tests/test_stabilize_ready.py tests/test_setup.py
rtk git commit -m "fix(setup): anchor same-boot hard reset sessions"
```

Expected: focused tests PASS and legacy reboot-anchor corpus guards remain green.

---

### Task 4: Propagate reset policy through plan setup and campaign restoration

**Files:**
- Modify: `plan.py:672-755,818-891`
- Modify: `tests/test_plan_execute.py`
- Modify: `tests/test_plan_campaign_restore.py`

**Interfaces:**
- Consumes: `plan.execution["reset_mode"]` from Task 1.
- Produces: each runtime `setup` passed to `SetupManager.run_setup()` contains the selected reset mode without mutating the loaded case profile.
- Produces: final `_campaign_cfg["reset_mode"]` always uses the plan selection, even when the final case has no setup section.

- [ ] **Step 1: Write failing per-case propagation test**

Create a temporary case with `setup.edgeconf_changes` and use a mock setup manager.
Assert the real plan loop passes a copied setup dictionary:

```python
plan = self._plan(
    {"regression": ["case_a"]},
    reset_mode="camera_hard",
)
execute_plan(plan, self.tmpdir, ssh_factory=ssh_factory,
             setup_factory=setup_factory, engine_factory=engine_factory)
setup_arg = created[0].run_setup.call_args.args[0]
self.assertEqual(setup_arg["reset_mode"], "camera_hard")
```

Also reload the case profile after execution and assert it has no `reset_mode`,
proving no source/profile mutation leaked.

- [ ] **Step 2: Write failing final-restore propagation tests**

In `tests/test_plan_campaign_restore.py`, cover both endings:

```python
def test_campaign_restore_keeps_plan_reset_mode_when_last_case_has_no_setup(self):
    # case_cfg changes edgeconf; case_readonly has no setup
    # capture the setup_config passed to run_teardown
    assert teardown_setup["reset_mode"] == "camera_hard"

def test_campaign_restore_passes_ord_union_for_reboot_downgrade(self):
    # case_ord changes ord_vcm; later edge-only case ends campaign
    assert teardown_setup["ord_vcm_changes"]
    assert teardown_setup["reset_mode"] == "camera_hard"
```

The second test keeps the requested mode as `camera_hard`; `SetupManager` performs
the effective reboot downgrade based on the campaign ORD union.

- [ ] **Step 3: Run plan execution tests and verify RED**

Run:

```bash
rtk pytest tests/test_plan_execute.py tests/test_plan_campaign_restore.py -q
```

Expected: FAIL because runtime setup and campaign restore lack the plan policy.

- [ ] **Step 4: Implement copy-on-injection and explicit campaign policy**

Immediately after runtime profile resolution in `execute_plan()`:

```python
setup_cfg = runtime.get("setup")
if setup_cfg:
    runtime = copy.deepcopy(runtime)
    runtime["setup"]["reset_mode"] = plan.execution.get("reset_mode", "reboot")
```

Before calling final `run_teardown()`:

```python
_campaign_cfg["reset_mode"] = plan.execution.get("reset_mode", "reboot")
```

Set this only inside the existing campaign cleanup guard; a plan that changed no
configuration must still avoid teardown and reset.

- [ ] **Step 5: Run focused plan tests and commit**

Run:

```bash
rtk pytest tests/test_plan_execute.py tests/test_plan_campaign_restore.py tests/test_integration_teardown_recovery.py -q
rtk git diff --check
rtk git add plan.py tests/test_plan_execute.py tests/test_plan_campaign_restore.py
rtk git commit -m "feat(plan): propagate reset mode through campaign restore"
```

---

### Task 5: Acceptance plan and operator documentation

**Files:**
- Create: `profiles/plans/hard_reset_acceptance.yaml`
- Create: `profiles/cases/camera_reset_postcheck.yaml`
- Create: `tests/test_cases_camera_reset_postcheck.py`
- Modify: `profiles/plans/_template.yaml`
- Modify: `profiles/plans/AGENTS.md`
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_plan_resolve.py`

**Interfaces:**
- Produces: repeatable plan `hard_reset_acceptance` with three ordered cases.
- Produces: read-only case `camera_reset_postcheck` for service, video-node, and
  nonnegative module-reference verification after campaign restoration.
- Consumes: plan `execution.reset_mode` and CLI override from Tasks 1 and 4.

- [ ] **Step 1: Write the failing acceptance-plan corpus test**

Add to `tests/test_plan_resolve.py`:

```python
def test_hard_reset_acceptance_plan_has_required_transition_matrix(self):
    root = os.path.dirname(os.path.dirname(__file__))
    plan = load_plan(os.path.join(root, "profiles", "plans", "hard_reset_acceptance.yaml"))
    self.assertEqual(
        resolve_cases(plan, os.path.join(root, "profiles")),
        [
            ("regression", "multi_4ch_720p"),
            ("delta", "multi_1ch_0_fhd"),
            ("delta", "720p_2ch"),
        ],
    )
    self.assertEqual(plan.execution["reset_mode"], "camera_hard")
    self.assertTrue(plan.execution["stop_on_fail"])
    self.assertEqual(plan.gate["threshold_pass_rate"], 1.0)
```

- [ ] **Step 2: Run the corpus test and verify RED**

Run:

```bash
rtk pytest tests/test_plan_resolve.py -q
```

Expected: FAIL because the acceptance plan does not exist.

- [ ] **Step 3: Create the acceptance plan**

Create `profiles/plans/hard_reset_acceptance.yaml` with exact content:

```yaml
name: "Camera Hard Reset Acceptance"
description: |
  A/B control plan for camera-only 4ch 720p -> 1ch FHD transitions plus an
  ORD/VCM mutation that must downgrade directly to reboot.
version: 1

cases:
  regression:
    - multi_4ch_720p
  delta:
    - multi_1ch_0_fhd
    - 720p_2ch

execution:
  reset_mode: camera_hard
  stop_on_fail: true
  case_retry: 0
  retry_wait_sec: 0
  reboot_wait_sec: 300
  wait_between_cases: 0
  monitor_until_pass: true

gate:
  threshold_pass_rate: 1.0
  allow_known_issue: false

reports:
  - format: json
    path: reports/hard_reset_acceptance/{timestamp}.json
  - format: markdown_summary
    path: reports/hard_reset_acceptance/{timestamp}.md
```

- [ ] **Step 4: Update operator contracts**

Create `profiles/cases/camera_reset_postcheck.yaml`:

```yaml
name: "camera reset postcheck"
description: "Read-only post-reset service, video-node, and module-refcount health"
tags: [camera, reset, postcheck]

monitor:
  duration_sec: 0

checks:
  custom_commands:
    - name: cam-operate service active
      command: systemctl is-active cam-operate.service
      expected: active
      on_fail: cam-operate.service is not active after reset/restore
    - name: required video nodes present
      command: test -e /dev/video3 -a -e /dev/video4 && echo PRESENT || echo MISSING
      expected: PRESENT
      on_fail: required camera video nodes were not recreated
    - name: camera module refcounts nonnegative
      command: "N=$(lsmod | awk '$1==\"max9296\" || $1==\"imx8_media_dev\" {if ($3+0 < 0) n++} END {print n+0}'); echo $N"
      expected: "0"
      on_fail: camera module has a negative reference count and requires reboot
```

Create `tests/test_cases_camera_reset_postcheck.py` with the read-only corpus
contract:

```python
from config import load_profile
from pim_check import PROFILES_DIR


def test_camera_reset_postcheck_is_read_only_and_complete():
    profile = load_profile(PROFILES_DIR, case="camera_reset_postcheck")
    assert "setup" not in profile
    assert profile["monitor"]["duration_sec"] == 0
    names = [item["name"] for item in profile["checks"]["custom_commands"]]
    assert names == [
        "cam-operate service active",
        "required video nodes present",
        "camera module refcounts nonnegative",
    ]
```

This keeps the postcheck read-only and prevents accidental reset recursion.

Add `reset_mode` with its two allowed values to `_template.yaml` and
`profiles/plans/AGENTS.md`. Update root `AGENTS.md` so the setup invariant reads:

```text
backup → apply → selected reset → test → restore → selected reset
```

Document that selected reset is reboot by default, camera hard reset is
edgeconf-only, and ORD/VCM or hard-reset failure uses reboot. Add a CHANGELOG
entry describing the opt-in strategy, same-boot anchor, and fallback behavior.

- [ ] **Step 5: Lint, test, and commit plan/docs**

Run:

```bash
rtk python3 -c "from plan import load_plan; load_plan('profiles/plans/hard_reset_acceptance.yaml'); print('plan lint ok')"
rtk pytest tests/test_plan_resolve.py tests/test_plan_load.py tests/test_cases_camera_reset_postcheck.py -q
rtk git diff --check
rtk git add profiles/plans/hard_reset_acceptance.yaml profiles/cases/camera_reset_postcheck.yaml profiles/plans/_template.yaml profiles/plans/AGENTS.md AGENTS.md CHANGELOG.md tests/test_plan_resolve.py tests/test_cases_camera_reset_postcheck.py
rtk git commit -m "docs(plan): add camera hard reset acceptance gate"
```

---

### Task 6: Full local verification

**Files:**
- Verify only; fix only failures caused by Tasks 1--5.

**Interfaces:**
- Consumes: all implementation and mocked tests.
- Produces: clean local verification evidence before any board lease.

- [ ] **Step 1: Run formatting and static checks**

Run:

```bash
rtk ruff check
rtk git diff --check
```

Expected: both commands exit 0 with no errors.

- [ ] **Step 2: Run the full mocked suite**

Run:

```bash
rtk pytest
```

Expected: all tests PASS; baseline before implementation was `2471 passed`.

- [ ] **Step 3: Inspect source status and commit any verification-only fixes**

Run:

```bash
rtk git status --short --branch
rtk git diff --stat origin/main...HEAD
```

If Tasks 1--5 caused a failure, return to the responsible task, add a failing
regression test when behavior changes, run that task's focused command, and commit
only the exact files named by that task. Do not use a directory-wide `git add`.

Expected: clean worktree before hardware acceptance.

---

### Task 7: Controlled reboot-versus-hard-reset acceptance

**Files:**
- Runtime reports only under ignored `reports/hard_reset_acceptance/`.
- No source mutation until both runs and restoration checks finish.

**Interfaces:**
- Consumes: `hard_reset_acceptance`, fixed wired host `192.168.214.4`, board lease wrapper.
- Produces: paired control/candidate report JSON and observable reset-path evidence.

- [ ] **Step 1: Confirm board lease is free without mutating it**

Run:

```bash
rtk jhw-control board status
```

Expected: no active holder. If BUSY, stop and report exit 4 semantics; do not wait,
retry, or acquire directly.

- [ ] **Step 2: Run the reboot control plan under a lease**

Run:

```bash
rtk scripts/with_pim_board.sh --for 45m --purpose "camera hard reset acceptance reboot control" -- python3 pim_check.py --plan hard_reset_acceptance --reset-mode reboot --host 192.168.214.4
```

Expected: gate PASS, 3/3 cases PASS, setup and final restoration use reboot, and
the wrapper releases the board.

- [ ] **Step 3: Run the camera-hard candidate under a new lease**

Run:

```bash
rtk scripts/with_pim_board.sh --for 45m --purpose "camera hard reset acceptance candidate" -- python3 pim_check.py --plan hard_reset_acceptance --reset-mode camera_hard --host 192.168.214.4
```

Expected: gate PASS, the first two setup transitions report effective
`camera_hard` with no fallback, the `720p_2ch` ORD/VCM transition reports direct
reboot downgrade, and final restoration uses reboot because the campaign touched
ORD/VCM.

- [ ] **Step 4: Compare paired report verdicts and durations**

Print and compare the two newest JSON reports with one deterministic command:

```bash
rtk python3 -c "import json; from pathlib import Path; paths=sorted(Path('reports/hard_reset_acceptance').glob('*.json'), key=lambda p:p.stat().st_mtime)[-2:]; assert len(paths)==2; [(print(p), print(json.dumps({'gate': (d:=json.loads(p.read_text()))['gate'], 'executions': [{'case_name': e['case_name'], 'passed': e['passed'], 'duration_sec': e['duration_sec'], 'retries_used': e['retries_used']} for e in d['executions']]}, indent=2))) for p in paths]"
```

Expected: the command prints the exact control and candidate paths, identical PASS
verdicts, and fewer transition seconds for the two camera-only candidate cases.

- [ ] **Step 5: Confirm post-run service and module health under a short lease**

Run:

```bash
rtk scripts/with_pim_board.sh --for 10m --purpose "camera hard reset acceptance postcheck" -- python3 pim_check.py --case camera_reset_postcheck --host 192.168.214.4
```

Expected: camera processes and board checks PASS, no negative MAX9296/media module
reference count appears in output, and the lease releases cleanly. If a hard-reset
fallback occurred or either plan failed, do not switch comprehensive.

---

### Task 8: Switch and validate comprehensive

**Files:**
- Modify: `profiles/plans/comprehensive.yaml`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_plan_resolve.py`
- Runtime reports only under ignored `reports/comprehensive/`.

**Interfaces:**
- Consumes: successful Task 7 control/candidate acceptance.
- Produces: comprehensive default `execution.reset_mode: camera_hard` with the
  existing 100% release gate unchanged.

- [ ] **Step 1: Write the failing comprehensive-policy test**

Add to `tests/test_plan_resolve.py`:

```python
def test_comprehensive_uses_camera_hard_reset_policy(self):
    root = os.path.dirname(os.path.dirname(__file__))
    plan = load_plan(os.path.join(root, "profiles", "plans", "comprehensive.yaml"))
    self.assertEqual(plan.execution["reset_mode"], "camera_hard")
    self.assertEqual(plan.gate["threshold_pass_rate"], 1.0)
```

- [ ] **Step 2: Run the policy test and verify RED**

Run:

```bash
rtk pytest tests/test_plan_resolve.py::TestResolveCases::test_comprehensive_uses_camera_hard_reset_policy -q
```

Expected: FAIL because comprehensive still defaults to reboot.

- [ ] **Step 3: Switch comprehensive and document the acceptance evidence**

Add this line under `profiles/plans/comprehensive.yaml` `execution:` without
changing cases, retries, monitor policy, or gate:

```yaml
  reset_mode: camera_hard
```

Append the paired acceptance report filenames and verdicts to the current
CHANGELOG entry. Do not commit runtime report files.

- [ ] **Step 4: Verify the profile change locally and commit**

Run:

```bash
rtk python3 -c "from plan import load_plan; load_plan('profiles/plans/comprehensive.yaml'); print('plan lint ok')"
rtk pytest tests/test_plan_resolve.py tests/test_plan_load.py -q
rtk git diff --check
rtk git add profiles/plans/comprehensive.yaml CHANGELOG.md tests/test_plan_resolve.py
rtk git commit -m "perf(plan): use camera hard reset for comprehensive"
```

- [ ] **Step 5: Run the full comprehensive release gate under a lease**

Run:

```bash
rtk scripts/with_pim_board.sh --for 3h --purpose "camera hard reset comprehensive validation" -- python3 pim_check.py --plan comprehensive --host 192.168.214.4
```

Expected: 21/21 PASS, gate pass rate 1.0, 16 multi-channel setup transitions use
camera hard reset without fallback, four ORD/VCM regression transitions and final
restoration use reboot, and the board lease releases cleanly. Any fallback is
reported as degraded optimization evidence and must be investigated before merge,
even if the release gate remains correct through reboot.

- [ ] **Step 6: Run final local verification from the final source tree**

Run:

```bash
rtk ruff check
rtk pytest
rtk git diff --check
rtk git status --short --branch
```

Expected: ruff exits 0, all pytest tests PASS, no whitespace errors, and the
worktree is clean.

---

### Task 9: Review-ready handoff

**Files:**
- Verify Git history and report exact runtime artifacts; no new source files.

**Interfaces:**
- Produces: branch ready for code review without creating or merging a PR unless
  the user explicitly requests that external action.

- [ ] **Step 1: Audit commits and changed files**

Run:

```bash
rtk git log --oneline origin/main..HEAD
rtk git diff --stat origin/main...HEAD
rtk git status --short --branch
```

Expected: only approved reset strategy, tests, acceptance plan, and documentation
are present; status is clean.

- [ ] **Step 2: Report verification and residual constraints**

Report exact pytest/ruff totals, paired acceptance report paths, comprehensive
report paths and duration, effective hard-reset count, reboot downgrade count,
fallback count, and board release state. State explicitly that fixed 300-second
monitor windows and ORD/VCM reboots remain.
