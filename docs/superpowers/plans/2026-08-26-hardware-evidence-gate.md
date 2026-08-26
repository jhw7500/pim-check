# Hardware Evidence Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** Add a trusted, board-leased hardware gate that can only report PASS when BPS and mixed-combo producers emit complete numeric evidence matching a committed baseline, the target identity is verified, and the exact pre-run board configuration is restored.

**Architecture:** A default-branch self-hosted workflow prepares a trusted run envelope, holds one three-hour PIM board lease, and executes a fail-closed `hw_gate` package. `BaseCheck`-derived collectors produce raw measurements, adapters normalize them, the central evaluator applies committed rules, and an atomic finalizer writes JSON/Markdown for PASS, FAIL, ERROR, or BUSY. A separate GitHub-hosted `workflow_run` publisher validates run-to-PR-to-HEAD binding and upserts one marker comment without giving the board-facing job write permission.

**Tech Stack:** Python 3.9+, pytest, PyYAML, existing Paramiko-backed `SshClient`, existing `SetupManager`, Bash board wrapper, GitHub Actions, GitHub REST API through Python stdlib.

**Spec:** `docs/superpowers/specs/2026-08-26-hardware-evidence-gate-design.md`

## Global Constraints

- Every agent-run shell command begins with `rtk`.
- Every new Python file starts with `from __future__ import annotations` and remains Python 3.9 compatible.
- Every hardware command runs through `scripts/with_pim_board.sh` with an explicit `--for` and `--purpose`. Exit 4 means BUSY and stops the attempt; never wait, retry acquisition, call `jhw-control board acquire`, or append `|| true`.
- New target commands use `SshClient.run()`. Do not add direct `ssh`, `sshpass`, or SSH-oriented `subprocess` calls.
- Every target mutation follows snapshot/backup -> persistent journal -> apply -> reboot -> measure -> exact restore -> reboot -> hash/read-back verification.
- `qp_min=[0,0]` and `qp_max=[0,0]` are the empirical BPS determinism requirement. `quant=[-1,-1]` is a controlled auto-initial-QP precondition, not a claimed causal fix.
- Exit code 0 is metadata only. A gate with missing, duplicate, non-numeric, non-finite, new, or unbaselined metrics recomputes to ERROR.
- The implementation does not deploy a PR DTB or kernel module. Evidence and comments must retain the phrase `predeployed measurement` and `deployment.verified=false`.
- Runtime evidence lives under ignored `hw-results/`; only the human-reviewed `baselines/hw-baseline.json` is committed.
- Official actions use these immutable commits, verified from their official repositories on 2026-08-26:
  - `actions/checkout@11d5960a326750d5838078e36cf38b85af677262` (`v4.4.0`)
  - `actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02` (`v4.6.2`)
  - `actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093` (`v4.3.0`)
- Do not merge or close issue #115 until the post-merge default-branch workflow and controlled hardware acceptance both pass.

## Planned File Structure

```text
hw_gate/
  __init__.py                 public version and verdict exports
  __main__.py                 python -m hw_gate entry point
  cli.py                      envelope, measure, finalize, validate, calibrate commands
  evidence.py                 canonical schema validation and verdict recomputation
  rules.py                    exact/range/relative numeric evaluator
  baseline.py                 committed baseline loading, coverage, candidate validation
  transaction.py              strict SetupManager wrapper and persistent recovery journal
  diagnostics.py              bounded allowlisted read-only failure collection
  render.py                   deterministic Markdown evidence renderer
  calibration.py              three-run BPS eligibility and candidate construction
  publisher.py                trusted workflow_run binding and marker comment upsert
  adapters/
    __init__.py               adapter registry
    base.py                   AdapterContext and HardwareGateAdapter protocol
    bps.py                    deterministic four-setpoint BPS orchestration/normalization
    mixed_combo.py            A/B/C/D scenario orchestration/normalization
checks/
  target_identity.py          allowlisted SHA/version collection
  bps_evidence.py             finalized-file and ffprobe numeric collection
  mixed_combo_evidence.py     mode and ISP register numeric collection
baselines/
  hw-baseline.template.json   reviewable descriptors and expected metric inventory
  hw-baseline.json            hardware-calibrated committed baseline
scripts/
  publish_hw_evidence.py      thin publisher executable
.github/workflows/
  hw-evidence-measure.yml     trusted self-hosted read-only measurement
  hw-evidence-publish.yml     GitHub-hosted write-scoped publication
tests/fixtures/hw_gate/
  baseline.json
  bps_raw_pass.json
  mixed_combo_raw_pass.json
  evidence_pass.json
tests/
  test_hw_gate_rules.py
  test_hw_gate_evidence.py
  test_hw_gate_baseline.py
  test_hw_gate_transaction.py
  test_checks_target_identity.py
  test_checks_bps_evidence.py
  test_checks_mixed_combo_evidence.py
  test_hw_gate_adapter_bps.py
  test_hw_gate_adapter_mixed_combo.py
  test_cases_bps_fixture.py
  test_hw_gate_cli.py
  test_hw_gate_calibration.py
  test_hw_gate_publisher.py
  test_integration_hw_evidence_workflows.py
```

---

### Task 1: Build the numeric rule engine and canonical evidence model

**Files:**

- Create: `hw_gate/__init__.py`
- Create: `hw_gate/rules.py`
- Create: `hw_gate/evidence.py`
- Create: `tests/test_hw_gate_rules.py`
- Create: `tests/test_hw_gate_evidence.py`
- Create: `tests/fixtures/hw_gate/evidence_pass.json`

**Contract:**

```python
class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    BUSY = "BUSY"
    STALE = "STALE"


def evaluate_rule(value: int | float, unit: str, baseline_metric: dict) -> dict:
    """Return normalized baseline_value, rule, delta, and PASS/FAIL verdict."""


def validate_structure(document: dict) -> None:
    """Raise EvidenceError for an unsafe or malformed schema-v1 document."""


def recompute_overall_verdict(document: dict, baseline: dict | None) -> Verdict:
    """Fail closed; producer-declared verdict never overrides recomputation."""
```

The canonical gate shape is fixed as follows:

```json
{
  "id": "bps_quick",
  "adapter_id": "bps_quick",
  "adapter_schema_version": 1,
  "process": {"exit_code": 0},
  "raw_output": {"path": "raw/bps_quick.json", "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
  "preconditions": [
    {"id": "ch0.qp_min", "expected": [0, 0], "observed": [0, 0], "verdict": "PASS"}
  ],
  "metrics": [
    {
      "id": "bps.ch0.1024.baseline",
      "value": 1025000,
      "unit": "bps",
      "baseline_value": 1024000,
      "rule": {"kind": "relative", "reference": 1024000, "max_percent_delta": 5.0},
      "delta": {"absolute": 1000, "percent": 0.09765625},
      "verdict": "PASS"
    }
  ],
  "restoration": {"cycles": [], "verdict": "PASS"},
  "diagnostic_refs": [],
  "errors": [],
  "verdict": "PASS"
}
```

Each BPS observation is asserted twice with distinct stable IDs: `.target` uses the configured target and 10% rule; `.baseline` uses the calibrated median and 5% rule. This preserves the approved singular `rule` field without hiding a compound assertion.

- [ ] Write RED tests for exact equality, inclusive range boundaries, relative percentage and absolute bounds, zero references, bool rejection, numeric-string rejection, NaN/infinity rejection, and unit mismatch.
- [ ] Run `rtk pytest tests/test_hw_gate_rules.py -q`; expect import failure because `hw_gate.rules` does not exist.
- [ ] Implement `evaluate_rule()` using finite `int`/`float` values while explicitly rejecting `bool`. Use `math.isfinite`, preserve full-precision deltas in JSON, and never round before verdict evaluation.
- [ ] Write RED evidence tests for valid PASS, mandatory `deployment.mode=predeployed` with `verified=false`, duplicate gate/metric IDs, producer PASS with zero metrics, missing/new baseline metrics, mismatched adapter schema, bad timestamps, invalid SHA fields, PASS with failed precondition/restoration/identity, ERROR precedence over FAIL, and BUSY as the only zero-gate terminal state.
- [ ] Run `rtk pytest tests/test_hw_gate_evidence.py -q`; expect import failure because `hw_gate.evidence` does not exist.
- [ ] Implement strict schema-v1 validation. Every gate, including ERROR gates, must contain at least one finite numeric metric. A partial producer uses stable `evidence.observed_metric_count` or `infrastructure.child_exit_code` evidence plus an `errors` entry; because that metric is not a substitute for the committed hardware metric set, baseline coverage still recomputes ERROR. A BUSY document must have zero gates, `board.lease_exit_code=4`, and no PASS claim.
- [ ] Implement precedence `ERROR > FAIL > PASS`; STALE is publisher-only and BUSY is allowed only from the lease finalizer.
- [ ] Run `rtk pytest tests/test_hw_gate_rules.py tests/test_hw_gate_evidence.py -q`; expect all tests to pass.
- [ ] Run `rtk ruff check hw_gate tests/test_hw_gate_rules.py tests/test_hw_gate_evidence.py`; expect no findings.
- [ ] Commit with `rtk git add hw_gate tests/test_hw_gate_rules.py tests/test_hw_gate_evidence.py tests/fixtures/hw_gate/evidence_pass.json && rtk git commit -m "feat(hw-gate): add fail-closed evidence model"`.

### Task 2: Add baseline loading, coverage, and template contracts

**Files:**

- Create: `hw_gate/baseline.py`
- Create: `baselines/hw-baseline.template.json`
- Create: `tests/test_hw_gate_baseline.py`
- Create: `tests/fixtures/hw_gate/baseline.json`
- Modify: `hw_gate/evidence.py`

**Baseline schema:**

```json
{
  "schema_version": 1,
  "baseline_version": "2026-08-26.1",
  "source_commit": "0000000000000000000000000000000000000000",
  "comparability": {
    "board_id": "pim",
    "target_host": "192.168.0.5",
    "bps_fixture": "multi_1ch_0_720p",
    "encoder": "h265"
  },
  "target_identity": [],
  "gates": {
    "bps_quick": {"adapter_schema_version": 1, "comparability": {}, "metrics": {}},
    "mixed_combo": {"adapter_schema_version": 1, "comparability": {}, "metrics": {}}
  },
  "calibration": {"bps": {"source_run_ids": [], "samples": {}}}
}
```

The template contains the complete mixed-combo exact metric inventory and BPS target rules, but uses an explicit `calibration_required` marker instead of pretending an unmeasured BPS median is a baseline. Production `load_baseline()` rejects that marker; only `calibration.py` accepts it as input.

- [ ] Write RED tests for baseline SHA256, schema/version/type checks, at least one identity claim, exact gate/metric set comparison, wrong units, missing/new metrics, duplicate identity IDs, comparability mismatch, calibration markers in production, and missing baseline -> candidate-required ERROR.
- [ ] Run `rtk pytest tests/test_hw_gate_baseline.py -q`; expect import failure.
- [ ] Implement `load_baseline(path) -> LoadedBaseline`, `validate_baseline(data, production=True)`, `baseline_sha256(path)`, and `assert_gate_coverage(raw_gate, baseline_gate)`.
- [ ] Encode four BPS target pairs for 1024/2048/4096/8192 kbps and exact mixed metrics with stable IDs `mixed_combo.test{1..4}.bus{1..2}.mode_mask` plus `mixed_combo.test{1..4}.ch{0..3}.{rotation,ae,awb}` for only the channels present in each scenario.
- [ ] Reject all unknown top-level, identity, gate, metric, and rule keys so a typo cannot silently weaken policy.
- [ ] Connect `recompute_overall_verdict()` to exact baseline coverage rather than trusting artifact-embedded rules.
- [ ] Run `rtk pytest tests/test_hw_gate_rules.py tests/test_hw_gate_evidence.py tests/test_hw_gate_baseline.py -q`; expect all tests to pass.
- [ ] Commit with `rtk git add hw_gate baselines tests/test_hw_gate_baseline.py tests/fixtures/hw_gate/baseline.json && rtk git commit -m "feat(hw-gate): define committed baseline contract"`.

### Task 3: Add scoped BaseCheck collectors and remove direct-SSH measurement paths

**Files:**

- Modify: `checks/base_check.py`
- Modify: `checks/__init__.py`
- Modify: `engine.py`
- Create: `checks/target_identity.py`
- Create: `checks/bps_evidence.py`
- Create: `checks/mixed_combo_evidence.py`
- Create: `tests/test_checks_target_identity.py`
- Create: `tests/test_checks_bps_evidence.py`
- Create: `tests/test_checks_mixed_combo_evidence.py`
- Modify: `tests/test_engine.py`

**Registry contract:**

```python
class BaseCheck(ABC):
    name: str = "unnamed"
    scope: str = "snapshot"


def checks_for_scope(scope: str) -> list[BaseCheck]:
    return [check for check in ALL_CHECKS if check.scope == scope]
```

`Engine` uses only `checks_for_scope("snapshot")`; all three new collectors declare `scope="hardware_evidence"` and are still registered in `ALL_CHECKS` as required by `checks/AGENTS.md`.

- [ ] Write a RED engine test proving adding hardware-evidence checks does not change normal snapshot results and that all new checks are discoverable in the hardware scope.
- [ ] Implement the scoped registry and update `Engine.__init__`.
- [ ] Write RED identity tests for a valid `max9296` module version/SHA, missing `modinfo`, malformed module names, paths outside `/boot`, `/lib/modules`, and `/root/shared_v`, SHA mismatch, and no claims.
- [ ] Implement `TargetIdentityCheck`: only `module_sha256`, `module_version`, and `file_sha256` descriptor kinds are accepted; validate module names with `^[A-Za-z0-9_-]+$`, validate resolved paths before `sha256sum`, and use only `SshClient.run()`.
- [ ] Write RED BPS collector tests using mocked `ssh.run`: read boot ID and board epoch, discover a finalized `*-ch0.mp4` with mtime at or after the setpoint anchor, require size >=100000 bytes, parse exactly one finite positive integer from `ffprobe`, and reject stale, `.part`, missing, or malformed files.
- [ ] Implement `BpsEvidenceCheck.collect()` as a bounded poll with injected clock/sleeper for tests; `validate()` checks evidence integrity only and owns no tolerance policy.
- [ ] Write RED mixed-combo tests for mode-mask parsing (`0`, `1`, `2`, `3`), I2C hex-word conversion to integers, missing ROTATION/AE/AWB, wrong address-mode response, and all A/B/C/D fixtures.
- [ ] Implement `MixedComboEvidenceCheck.collect()` and `validate()` using `SshClient.run()` for `i2cdetect` and `i2ctransfer`; return numeric register words and mode masks, not booleans as authoritative evidence.
- [ ] Run `rtk pytest tests/test_engine.py tests/test_checks_target_identity.py tests/test_checks_bps_evidence.py tests/test_checks_mixed_combo_evidence.py -q`; expect all tests to pass.
- [ ] Run `rtk rg -n "sshpass|subprocess.*ssh|\[.*ssh.*root@" checks/target_identity.py checks/bps_evidence.py checks/mixed_combo_evidence.py`; expect no matches.
- [ ] Commit with `rtk git add checks engine.py tests/test_engine.py tests/test_checks_target_identity.py tests/test_checks_bps_evidence.py tests/test_checks_mixed_combo_evidence.py && rtk git commit -m "feat(checks): add hardware evidence collectors"`.

### Task 4: Implement strict mutation, persistent recovery, and verified restoration

**Files:**

- Modify: `setup.py`
- Create: `hw_gate/transaction.py`
- Create: `tests/test_hw_gate_transaction.py`
- Modify: `tests/test_setup_snapshot.py`

**Public snapshot addition:**

```python
def get_snapshot_payload(self, conf_path: str) -> str | None:
    """Return the validated base64 snapshot captured by snapshot_config()."""
    return self._config_snapshots.get(conf_path)
```

**Transaction states:** `NEW -> SNAPSHOTTED -> JOURNALED -> APPLIED -> REBOOTED -> RESTORED -> VERIFIED -> CLOSED`.

The remote journal root is exactly `/root/shared_v/pim-check-recovery`. Each run directory is mode 700 and contains mode-600 `edgeconf_pim.json.original` plus `manifest.json`. The manifest contains schema version, sanitized run ID, config path, original SHA256, creation timestamp, and transaction state; never credentials or the full config inline.

- [ ] Add RED `SetupManager` tests for read-only snapshot payload export and no export after failed snapshot.
- [ ] Add RED transaction tests proving snapshot failure, persistent-copy/hash failure, or config-guard backup failure aborts before `apply_changes()`.
- [ ] Add RED lifecycle tests for apply, reboot, body/measurement, parse, assertion, SIGTERM-style exception, restore, and post-restore reboot failures. Every path after mutation must attempt restoration; restore/hash/readiness failure must override a prior PASS with ERROR.
- [ ] Add RED dirty-journal tests: no journal is a no-op; one valid journal restores the persistent original, reboots, verifies its SHA, and deletes only that journal; malformed or multiple journals emit ERROR and block measurement; a recovery failure leaves the journal intact.
- [ ] Implement `StrictHardwareTransaction` around `SetupManager.snapshot_config()`, `get_snapshot_payload()`, `backup()`, `apply_changes()`, `reboot_and_wait()`, and `restore_from_snapshot()`. Do not call lenient `run_setup()`.
- [ ] Persist and validate the journal before the first mutation. Use base64 transport already validated by `SetupManager`, atomic temporary files, `jq -e`, `sha256sum`, `chmod`, and `sync` through `SshClient.run()`.
- [ ] Implement `recover_pending_transaction()` before identity probing. Recovery uses the journal copy, not an in-memory snapshot, and verifies full-file SHA after reboot before deletion.
- [ ] Make context-manager `__exit__` and explicit signal-triggered unwinding share one idempotent `restore_and_verify()` path.
- [ ] Run `rtk pytest tests/test_setup_snapshot.py tests/test_hw_gate_transaction.py -q`; expect all tests to pass.
- [ ] Commit with `rtk git add setup.py hw_gate/transaction.py tests/test_setup_snapshot.py tests/test_hw_gate_transaction.py && rtk git commit -m "feat(hw-gate): add crash-recoverable board transaction"`.

### Task 5: Implement deterministic BPS adapter and legacy compatibility shim

**Files:**

- Create: `hw_gate/adapters/__init__.py`
- Create: `hw_gate/adapters/base.py`
- Create: `hw_gate/adapters/bps.py`
- Create: `tests/test_hw_gate_adapter_bps.py`
- Create: `tests/test_cases_bps_fixture.py`
- Create: `tests/fixtures/hw_gate/bps_raw_pass.json`
- Modify: `profiles/cases/multi_1ch_0_720p.yaml`
- Modify: `run_bps_quick.py`

**Adapter interface:**

```python
@dataclass(frozen=True)
class AdapterContext:
    ssh: SshClient
    baseline_gate: dict
    run_id: str
    raw_dir: Path


class HardwareGateAdapter(Protocol):
    adapter_id: str
    schema_version: int

    def run(self, context: AdapterContext) -> dict:
        """Return canonical gate evidence; never decide policy from exit code alone."""
```

- [ ] Add `quant: [-1, -1]` beside the existing QP/profile fields in `multi_1ch_0_720p.yaml` and add a corpus assertion that the BPS fixture contains `enc=h265`, QP min/max zero, quant auto, profile zero, resolution/fps/muxer/recording/capture/exposure, and exactly ch0 enabled.
- [ ] Write RED adapter tests for the four exact setpoints, one full transaction per setpoint, full fixture apply, post-reboot read-back of every controlled field, fresh finalized video, numeric normalization, raw-output SHA, and eight BPS metrics (`.target` plus `.baseline`).
- [ ] Add RED failure tests for QP min/max mismatch, quant mismatch, profile mismatch, stale video, probe failure, missing baseline, target >10%, baseline >5%, and restore failure overriding a would-be PASS.
- [ ] Implement `BpsAdapter` by loading `load_profile("profiles", "multi_1ch_0_720p")`, copying its `setup.edgeconf_changes`, overriding only `.VHL_CAM.i2c2.ch0.bps` per setpoint, and executing an independent `StrictHardwareTransaction` for each setpoint.
- [ ] After each reboot, call `SetupManager.check_current()` on the complete fixture and record every expected/read-back pair as a precondition before accepting `actual_bps`.
- [ ] Preserve full-precision `actual_bps`; evaluate 10% target and 5% calibrated median as separate baseline-owned metrics. Remove the legacy 25%/10% policy from the authoritative gate.
- [ ] Replace `run_bps_quick.py` with a thin compatibility entry point that calls the adapter-backed local runner, writes `bps_quick_results.json`, and returns 0 only when central baseline evaluation and restoration pass. It must contain no SSH implementation or killcam path.
- [ ] Run `rtk pytest tests/test_hw_gate_adapter_bps.py tests/test_checks_bps_evidence.py tests/test_cases_bps_fixture.py -q`; expect all tests to pass. Create `tests/test_cases_bps_fixture.py` if no existing profile-corpus test is a clean fit.
- [ ] Run `rtk rg -n "killcam|sshpass|subprocess" run_bps_quick.py hw_gate/adapters/bps.py`; expect no matches.
- [ ] Commit with `rtk git add hw_gate/adapters profiles/cases/multi_1ch_0_720p.yaml run_bps_quick.py tests/test_hw_gate_adapter_bps.py tests/test_cases_bps_fixture.py tests/fixtures/hw_gate/bps_raw_pass.json && rtk git commit -m "feat(hw-gate): enforce deterministic BPS evidence"`.

### Task 6: Implement mixed-combo adapter and restore-safe legacy shim

**Files:**

- Create: `hw_gate/adapters/mixed_combo.py`
- Create: `tests/test_hw_gate_adapter_mixed_combo.py`
- Create: `tests/fixtures/hw_gate/mixed_combo_raw_pass.json`
- Modify: `run_mixed_combo_verify.py`
- Modify: `hw_gate/adapters/__init__.py`

- [ ] Write RED golden tests that preserve scenarios 1-4 and their A/B/C/D channel assignments while normalizing both bus mode masks and every enabled-channel ROTATION/AE/AWB word to exact numeric metrics.
- [ ] Write RED completeness tests proving omitted scenarios, channels, mode masks, or register values become ERROR even when legacy `all_pass=true` and exit code 0 are present.
- [ ] Write RED transaction tests proving one campaign snapshot/journal is created before scenario 1, every scenario applies a cleanroom config and reboots, and one `finally` restore/reboot/hash verification runs after scenario 4 or any earlier exception.
- [ ] Move the existing scenario constants and cleanroom change construction into `MixedComboAdapter`; use `SetupManager.apply_changes()` plus `check_current()` and `MixedComboEvidenceCheck`, never a generated jq shell pipeline.
- [ ] Store raw scenario output for diagnostics, calculate its SHA256, and let `baseline.py` own all exact comparisons.
- [ ] Replace `run_mixed_combo_verify.py` with the same thin adapter-backed compatibility pattern as BPS. Preserve `mixed_combo_results.json` for current workflow consumers but remove direct `sshpass`, ping polling, and non-restoring mutation logic.
- [ ] Run `rtk pytest tests/test_hw_gate_adapter_mixed_combo.py tests/test_checks_mixed_combo_evidence.py -q`; expect all tests to pass.
- [ ] Run `rtk rg -n "sshpass|subprocess.*ssh|def ssh\(" run_mixed_combo_verify.py hw_gate/adapters/mixed_combo.py`; expect no matches.
- [ ] Commit with `rtk git add hw_gate/adapters run_mixed_combo_verify.py tests/test_hw_gate_adapter_mixed_combo.py tests/fixtures/hw_gate/mixed_combo_raw_pass.json && rtk git commit -m "feat(hw-gate): normalize and restore mixed-combo runs"`.

### Task 7: Add orchestration CLI, partial-evidence finalizer, diagnostics, and Markdown

**Files:**

- Create: `hw_gate/__main__.py`
- Create: `hw_gate/cli.py`
- Create: `hw_gate/diagnostics.py`
- Create: `hw_gate/render.py`
- Create: `tests/test_hw_gate_cli.py`
- Modify: `.gitignore`

**CLI:**

```text
python3 -m hw_gate prepare   --repository --pr-number --pr-head-sha --workflow-run-id --workflow-run-attempt --source-commit --baseline --output-dir
python3 -m hw_gate measure   --envelope --target-host --output-dir [--gates bps_quick,mixed_combo]
python3 -m hw_gate finalize  --envelope --output-dir --child-exit-code
python3 -m hw_gate validate  --evidence --baseline
```

Exit codes are fixed: 0 PASS, 1 FAIL, 2 ERROR, 4 BUSY. STALE is a publisher presentation and has no measurement CLI exit code.

- [ ] Write RED parser tests for required full SHA/run fields, same repository format, positive PR/run/attempt integers, fixed target host, allowed gates, and rejection of unknown arguments.
- [ ] Write RED lifecycle tests for dirty-journal recovery before identity, baseline and source-commit binding, identity before adapters, deterministic adapter order, checkpoint after each state transition, diagnostics before lease release, and SSH close. Recovery or identity errors create numeric ERROR gates and stop before adapter mutation.
- [ ] Write RED finalizer tests: preserve a valid child artifact; convert wrapper exit 4 with no child artifact to canonical BUSY; convert other no-artifact exits to canonical ERROR with one `infrastructure` gate carrying numeric `infrastructure.child_exit_code` plus the exit/preflight error; never turn child exit 0 into PASS without complete baseline-backed gates.
- [ ] Implement atomic writes with `Path.replace()` from a same-directory mode-600 temporary file. Use `hw-results/${PR_HEAD_SHA}.json`, `.md`, `.candidate.json`, and `raw/${PR_HEAD_SHA}/`.
- [ ] Add `hw-results/` to `.gitignore`; do not ignore `baselines/`.
- [ ] Install SIGTERM and SIGHUP handlers that raise one internal termination exception. Let active transaction contexts restore, write ERROR/checkpoint evidence, then exit nonzero. Do not intercept SIGKILL.
- [ ] Implement allowlisted diagnostics with hard bounds: evidence JSON <=1,048,576 bytes, each raw tail <=16,384 bytes, dmesg <=200 lines, process list limited to declared names, selected edgeconf values only, and no full config or credentials.
- [ ] Render deterministic Markdown with `predeployed measurement`, full HEAD, baseline SHA, target identities, all metrics/rules/deltas, preconditions, restoration, diagnostics, and run URL. Escape Markdown/HTML control characters from all measured strings.
- [ ] Run `rtk pytest tests/test_hw_gate_cli.py tests/test_hw_gate_evidence.py -q`; expect all tests to pass.
- [ ] Run `rtk python3 -m hw_gate --help`; expect the four documented subcommands.
- [ ] Commit with `rtk git add hw_gate .gitignore tests/test_hw_gate_cli.py && rtk git commit -m "feat(hw-gate): orchestrate durable evidence artifacts"`.

### Task 8: Add three-run calibration and commit a human-reviewed baseline

**Files:**

- Create: `hw_gate/calibration.py`
- Create: `tests/test_hw_gate_calibration.py`
- Modify: `hw_gate/cli.py`
- Modify: `hw_gate/__main__.py`
- Create during controlled execution: `baselines/hw-baseline.json`

- [ ] Write RED tests requiring exactly three independent samples per setpoint, every sample within 10% of target, maximum sample-to-median deviation <=5%, median as committed reference, all source run IDs/samples retained, identity claims populated, and an explicitly ineligible candidate when any setpoint fails.
- [ ] Implement `build_candidate(template, calibration_runs)`. It may write only the requested candidate path and must refuse any output path resolving to `baselines/hw-baseline.json`.
- [ ] Add the fifth CLI subcommand, `calibrate --template --target-host --repetitions 3 --output`, and verify `rtk python3 -m hw_gate --help` lists it.
- [ ] Run `rtk pytest tests/test_hw_gate_calibration.py -q`; expect all tests to pass.
- [ ] Run all local tests through Task 8 before touching hardware: `rtk pytest tests/test_hw_gate_*.py tests/test_checks_bps_evidence.py tests/test_checks_mixed_combo_evidence.py tests/test_checks_target_identity.py -q`.
- [ ] With user-selected execution mode and a free board, run exactly one calibration lease:

```bash
rtk scripts/with_pim_board.sh --for 3h --purpose "pim-check#115 baseline calibration" -- \
  python3 -m hw_gate calibrate \
    --template baselines/hw-baseline.template.json \
    --target-host 192.168.0.5 \
    --repetitions 3 \
    --output hw-results/baseline-candidate.json
```

Expected: exit 0 and an eligible candidate containing three fully restored samples for every setpoint. If exit 4, stop as BUSY. If exit 2 or any setpoint violates 10%/5%, stop and report the samples; do not widen a threshold.

- [ ] Review `hw-results/baseline-candidate.json`, target identity SHA/version, source sample IDs, restoration hashes, units, and all exact mixed-combo expectations. Use `apply_patch` to add the reviewed bytes as `baselines/hw-baseline.json`; do not copy or auto-promote it from the tool.
- [ ] Update `tests/fixtures/hw_gate/evidence_pass.json` with the reviewed baseline's identity and metric references, then run `rtk python3 -m hw_gate validate --evidence tests/fixtures/hw_gate/evidence_pass.json --baseline baselines/hw-baseline.json`; expect exit 0.
- [ ] Run `rtk pytest tests/test_hw_gate_baseline.py tests/test_hw_gate_calibration.py tests/test_hw_gate_adapter_bps.py -q`; expect all tests to pass.
- [ ] Commit with `rtk git add hw_gate/calibration.py hw_gate/cli.py hw_gate/__main__.py tests/test_hw_gate_calibration.py baselines/hw-baseline.json tests/fixtures/hw_gate && rtk git commit -m "test(hw-gate): commit calibrated hardware baseline"`.

### Task 9: Implement trusted run binding and marker-based PR publication

**Files:**

- Create: `hw_gate/publisher.py`
- Create: `scripts/publish_hw_evidence.py`
- Create: `tests/test_hw_gate_publisher.py`

**Publisher trust inputs:** repository from `GITHUB_REPOSITORY`; workflow run ID/attempt/name/event/head SHA/actor/display title from GitHub API; and PR number parsed from strict trusted workflow metadata `^hw-evidence-pr-[0-9]+$`. For `pull_request_target`, require the fetched PR head repository to equal the current repository and cross-check `workflow_run.pull_requests` when GitHub supplies it. For `workflow_dispatch`, require the trusted source SHA to be on the default branch and the triggering actor to have write-or-higher permission. The artifact PR number is comparison data only and never the comment destination.

- [ ] Write RED tests for wrong repository, workflow name, run-name format, event, run ID, attempt, default-branch source SHA, actor permission, optional `workflow_run.pull_requests` disagreement, PR number, same-repository head repo, current head, evidence size, baseline SHA/source commit, internal binding, and producer/publisher verdict disagreement.
- [ ] Write RED malformed-artifact tests: with trusted run-to-PR binding, publish bounded ERROR; without trusted destination binding, fail without an API mutation.
- [ ] Write RED comment tests for marker `<!-- pim-check:hardware-evidence -->`, create when absent, update one bot-owned marker comment when present, no update of another author's lookalike, HTML escaping, and replacement of old PASS text with STALE when current PR HEAD differs.
- [ ] Implement a small injected `GithubClient` using `urllib.request`; support pagination and JSON size/time bounds. Never execute artifact strings or use `shell=True`.
- [ ] Fetch `baselines/hw-baseline.json` at the trusted measurement `head_sha`, verify its SHA256 against evidence, and recompute gates with current publisher code.
- [ ] Keep comments compact but complete: scope, verdict, head, run link, baseline SHA, identity, per-metric value/rule/delta, preconditions, restoration, bounded diagnostics.
- [ ] Add executable mode to the thin script with `rtk chmod +x scripts/publish_hw_evidence.py`.
- [ ] Run `rtk pytest tests/test_hw_gate_publisher.py -q`; expect all tests to pass.
- [ ] Commit with `rtk git add hw_gate/publisher.py scripts/publish_hw_evidence.py tests/test_hw_gate_publisher.py && rtk git commit -m "feat(hw-gate): publish trusted PR evidence"`.

### Task 10: Add measurement/publisher workflows and board-command guard coverage

**Files:**

- Create: `.github/workflows/hw-evidence-measure.yml`
- Create: `.github/workflows/hw-evidence-publish.yml`
- Create: `tests/test_integration_hw_evidence_workflows.py`
- Modify: `tests/test_integration_board_reservation.py`
- Modify: `scripts/guard_pim_board_command.py`
- Modify: `tests/test_guard_pim_board_command.py`

**Measurement workflow contract:**

- Name and run-name: `Hardware Evidence Measurement`, `hw-evidence-pr-${{ github.event.pull_request.number || inputs.pr_number }}`.
- Triggers: `pull_request_target` types `[labeled, synchronize]`; `workflow_dispatch` with required numeric `pr_number`.
- Job condition: same-repository PR, `needs-hw-verify` label, or a default-branch manual dispatch.
- Permissions: `contents: read`, `pull-requests: read`; no write scope.
- Checkout exact `${{ github.sha }}` from the trusted workflow context; never checkout PR HEAD.
- Concurrency: `{group: pim-target-lock, cancel-in-progress: false}`.
- One board-facing step, exactly `scripts/with_pim_board.sh --for 3h --purpose "github:${{ github.workflow }}:${{ github.run_id }}:${{ github.run_attempt }}:hw-evidence" -- python3 -m hw_gate measure --envelope hw-results/envelope.json --target-host "${TARGET_HOST}" --output-dir hw-results`.
- Prepare envelope before lease, capture child exit unchanged, run local finalizer, and always upload `hw-results/**` as `hw-evidence-${{ github.run_id }}-${{ github.run_attempt }}` with `if-no-files-found: error`.

**Publisher workflow contract:**

- Trigger only `workflow_run: workflows: [Hardware Evidence Measurement], types: [completed]`.
- GitHub-hosted `ubuntu-latest`; permissions `actions: read`, `contents: read`, `pull-requests: write`.
- Checkout current default-branch publisher code with the immutable checkout pin.
- Download only the exact triggering artifact using the immutable download pin and triggering run ID. Mark download `continue-on-error: true` so trusted publisher code can report missing/malformed artifact as ERROR.
- Run publisher even after measurement failure; it validates destination and evidence before commenting.

- [ ] Write RED YAML contract tests for both triggers, conditions, permissions, runner labels, exact action SHAs, trusted checkout ref, one lease, 3h duration, purpose, unchanged exit 4, finalizer, always upload, artifact name, hosted publisher, no PR write in measurement, and no self-hosted job in publisher.
- [ ] Add the new measurement workflow to `test_hardware_workflow_uses_common_fail_fast_wrapper()` with job `measure`, step `Run leased hardware evidence`, lease `3h`, and the exact purpose above.
- [ ] Write RED guard tests showing direct `python3 -m hw_gate measure` and `calibrate` are blocked while `prepare`, `finalize`, `validate`, and canonical-wrapper children are allowed; unknown or ambiguous subcommands fail closed.
- [ ] Extend the guard with `HW_GATE_HARDWARE_SUBCOMMANDS = {"measure", "calibrate"}`; parse static `python -m hw_gate` arguments and fail closed on missing, expanded, unknown, or ambiguous subcommands without weakening existing launcher recursion.
- [ ] Implement both workflows with pinned action commits and environment variables for PR/head/run data so untrusted event strings are never interpolated into executable shell fragments.
- [ ] Run `rtk pytest tests/test_integration_hw_evidence_workflows.py tests/test_integration_board_reservation.py tests/test_guard_pim_board_command.py -q`; expect all tests to pass.
- [ ] Parse both workflows with `rtk python3 -c "import pathlib,yaml; [yaml.safe_load(pathlib.Path(p).read_text()) for p in ('.github/workflows/hw-evidence-measure.yml','.github/workflows/hw-evidence-publish.yml')]"`; expect exit 0.
- [ ] Commit with `rtk git add .github/workflows scripts/guard_pim_board_command.py tests/test_integration_hw_evidence_workflows.py tests/test_integration_board_reservation.py tests/test_guard_pim_board_command.py && rtk git commit -m "ci(hw-gate): add trusted measurement and publisher split"`.

### Task 11: Document operations and run complete local verification

**Files:**

- Modify: `README.md`
- Modify: `.github/README.md`
- Modify: `AGENTS.md`
- Modify: `checks/AGENTS.md`
- Modify: `tests/AGENTS.md`
- Modify: `scripts/AGENTS.md`
- Modify: `docs/superpowers/specs/2026-08-26-hardware-evidence-gate-design.md` only if implementation revealed a reviewed contract clarification

- [ ] Document the label/manual triggers, predeployed scope, verdict meanings, baseline review process, no-secret requirement, 3h lease, BUSY exit 4, journal recovery, artifact paths, marker comment, bootstrap limitation, and phase-two deployment exclusion.
- [ ] Replace legacy README wording that describes BPS killcam/no-restore behavior with rebooted deterministic transactions and baseline evaluation.
- [ ] Update directory AGENTS tables for the new package, checks, scripts, workflows, baseline, and tests without weakening existing rules.
- [ ] Run `rtk git diff --check`; expect no whitespace errors.
- [ ] Run `rtk ruff check`; expect no findings.
- [ ] Run `rtk pytest`; expect the complete suite to pass with no physical target.
- [ ] Run `rtk rg -n "actions/(checkout|upload-artifact|download-artifact)@v[0-9]" .github/workflows/hw-evidence-*.yml`; expect no matches.
- [ ] Run `rtk rg -n "sshpass|subprocess.*ssh|board wait|jhw-control board acquire|\|\| true" hw_gate checks/target_identity.py checks/bps_evidence.py checks/mixed_combo_evidence.py .github/workflows/hw-evidence-*.yml`; expect no matches.
- [ ] Run `rtk python3 -m hw_gate validate --evidence tests/fixtures/hw_gate/evidence_pass.json --baseline tests/fixtures/hw_gate/baseline.json`; expect exit 0.
- [ ] Commit with `rtk git add README.md .github/README.md AGENTS.md checks/AGENTS.md tests/AGENTS.md scripts/AGENTS.md docs/superpowers/specs/2026-08-26-hardware-evidence-gate-design.md && rtk git commit -m "docs(hw-gate): document evidence operations"`.

### Task 12: Run controlled hardware acceptance without weakening policy

**Files:**

- Generate ignored evidence under: `hw-results/`
- Modify only if a real defect is found: implementation/tests from Tasks 1-11

Do not start this task until local pytest and ruff are green and the user has selected an execution mode. Each command below is a separate explicit lease; BUSY exit 4 stops that command without retry.

- [ ] Prepare a local bootstrap envelope bound to the current full branch SHA and reviewed baseline:

```bash
rtk python3 -m hw_gate prepare \
  --repository jhw7500/pim-check \
  --pr-number "$(rtk gh pr view --json number --jq .number)" \
  --pr-head-sha "$(rtk git rev-parse HEAD)" \
  --workflow-run-id "$(rtk date +%s)" \
  --workflow-run-attempt 1 \
  --source-commit "$(rtk git rev-parse HEAD)" \
  --baseline baselines/hw-baseline.json \
  --output-dir hw-results/acceptance
```
- [ ] Run the complete predeployed matrix:

```bash
rtk scripts/with_pim_board.sh --for 3h --purpose "pim-check#115 controlled acceptance" -- \
  python3 -m hw_gate measure \
    --envelope hw-results/acceptance/envelope.json \
    --target-host 192.168.0.5 \
    --output-dir hw-results/acceptance
```

Expected: exit 0, PASS JSON/Markdown, four BPS setpoints, all mixed metrics, exact identity, and every restore hash equal.

- [ ] Exercise supported termination with a BPS-only run by sending TERM to the leased wrapper after mutation:

```bash
rtk timeout --signal=TERM --kill-after=35m 8m \
  scripts/with_pim_board.sh --for 45m --purpose "pim-check#115 termination acceptance" -- \
  python3 -m hw_gate measure \
    --envelope hw-results/acceptance/envelope.json \
    --target-host 192.168.0.5 \
    --output-dir hw-results/termination \
    --gates bps_quick
```

Expected: timeout/nonzero result, ERROR evidence, released lease, restored hash, and a retained journal only if cleanup could not complete. Do not use SIGKILL for the primary cleanup assertion.
- [ ] Start one subsequent leased measure:

```bash
rtk scripts/with_pim_board.sh --for 90m --purpose "pim-check#115 recovery preflight acceptance" -- \
  python3 -m hw_gate measure \
    --envelope hw-results/acceptance/envelope.json \
    --target-host 192.168.0.5 \
    --output-dir hw-results/recovery-preflight \
    --gates bps_quick
```

Its first target action must recover and verify any dirty journal before identity or mutation; if recovery fails, expect ERROR and stop.
- [ ] Generate a wrong-identity baseline and its bound envelope:

```bash
rtk jq '(.target_identity[0].expected_sha256) |= (if startswith("0") then "1" + .[1:] else "0" + .[1:] end)' \
  baselines/hw-baseline.json > hw-results/wrong-identity-baseline.json
rtk python3 -m hw_gate prepare \
  --repository jhw7500/pim-check \
  --pr-number "$(rtk gh pr view --json number --jq .number)" \
  --pr-head-sha "$(rtk git rev-parse HEAD)" \
  --workflow-run-id "$(rtk date +%s)" \
  --workflow-run-attempt 1 \
  --source-commit "$(rtk git rev-parse HEAD)" \
  --baseline hw-results/wrong-identity-baseline.json \
  --output-dir hw-results/wrong-identity
rtk scripts/with_pim_board.sh --for 30m --purpose "pim-check#115 wrong identity acceptance" -- \
  python3 -m hw_gate measure \
    --envelope hw-results/wrong-identity/envelope.json \
    --target-host 192.168.0.5 \
    --output-dir hw-results/wrong-identity \
    --gates bps_quick
```

Expected: ERROR before target mutation and an unchanged target hash.

- [ ] Generate a tightened BPS baseline and its bound envelope:

```bash
rtk jq '(.gates.bps_quick.metrics["bps.ch0.1024.baseline"].rule.reference) *= 2' \
  baselines/hw-baseline.json > hw-results/tight-bps-baseline.json
rtk python3 -m hw_gate prepare \
  --repository jhw7500/pim-check \
  --pr-number "$(rtk gh pr view --json number --jq .number)" \
  --pr-head-sha "$(rtk git rev-parse HEAD)" \
  --workflow-run-id "$(rtk date +%s)" \
  --workflow-run-attempt 1 \
  --source-commit "$(rtk git rev-parse HEAD)" \
  --baseline hw-results/tight-bps-baseline.json \
  --output-dir hw-results/tight-bps
rtk scripts/with_pim_board.sh --for 90m --purpose "pim-check#115 tightened BPS acceptance" -- \
  python3 -m hw_gate measure \
    --envelope hw-results/tight-bps/envelope.json \
    --target-host 192.168.0.5 \
    --output-dir hw-results/tight-bps \
    --gates bps_quick
```

Expected: FAIL with numeric observed/reference/delta and verified restoration.
- [ ] Inspect JSON, Markdown, bounded diagnostics, raw SHA links, and absence of credentials/full edgeconf. Confirm every declared gate has at least one baseline-owned numeric metric and no exit-only PASS path.
- [ ] If implementation changes were needed, add a reproducing RED test first, implement the smallest fix, rerun targeted tests, `rtk ruff check`, and `rtk pytest`, then repeat only the failed acceptance case under a fresh explicit lease.
- [ ] Commit any test-driven fixes and retain acceptance artifact paths/run IDs in the PR description; do not commit `hw-results/`.

### Task 13: Finish PR review, then verify the post-merge trusted path

**Files:**

- No source changes unless review or post-merge verification exposes a tested defect

- [ ] Run `rtk git status --short`, `rtk git diff --check`, `rtk ruff check`, and `rtk pytest`; all must be clean/green.
- [ ] Invoke the repository review/ship workflow requested by the user. Do not use a quota exception for a hardware or restoration finding.
- [ ] Confirm the implementation PR comment and description say `predeployed measurement`, not that PR artifacts were deployed.
- [ ] After merge, open a same-repository follow-up test PR, add `needs-hw-verify`, and confirm the default-branch `pull_request_target` workflow starts trusted code, uses one lease, uploads evidence, and the hosted publisher creates exactly one marker comment.
- [ ] Push a harmless new commit to that follow-up PR while the label remains. Confirm the new run binds to the new full HEAD and the old PASS presentation is replaced by STALE until current evidence publishes.
- [ ] Trigger one manual default-branch dispatch with the same PR number. Confirm trusted run-name/actor binding, current HEAD validation, and marker upsert.
- [ ] Close issue #115 only after controlled hardware acceptance and this post-merge path both pass. Record automatic DTB/module deployment as a separate phase-two design task.

## Final Verification Matrix

| Invariant | Automated evidence | Hardware/rollout evidence |
|---|---|---|
| Exit 0 cannot pass without measurements | evidence, baseline, adapter contract tests | inspect complete matrix artifact |
| QP/quant deterministic BPS setup | fixture and adapter read-back tests | four setpoint preconditions in JSON |
| 10% target + 5% baseline | rule/calibration tests | tightened-baseline FAIL |
| Exact board restoration | transaction exception matrix | termination and next-run recovery |
| BUSY stays exit 4 | workflow/wrapper tests | stop immediately if board is busy |
| No PR-head code on self-hosted | workflow contract tests | Actions checkout/run inspection |
| Publisher cannot choose destination from artifact | publisher binding tests | marker create/upsert/stale run |
| Predeployed scope is honest | renderer golden tests | PR comment review |
| No automatic baseline promotion | calibration path refusal test | human-reviewed baseline commit |

## Stop Conditions

- Stop immediately on board exit 4; report BUSY without waiting or retrying.
- Stop before mutation if snapshot, journal, backup, baseline, identity, or recovery preflight is invalid.
- Stop baseline bootstrap if any sample misses the 10% target or 5% sample-to-median limits; do not relax policy.
- Stop PR merge on any unresolved restoration, identity, trust-boundary, missing-metric, or publisher-destination finding.
- Leave phase-two artifact deployment unimplemented and explicitly tracked after this plan is complete.
