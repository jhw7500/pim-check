# Task 8 baseline promotion report

## Scope and immutable inputs

This bounded promotion added the human-approved production baseline and its
deterministic PASS fixture only. No board lease, hardware command, candidate
generation, threshold tuning, runtime-code change, legacy WiFi-default change,
ETH0 behavior change, push, PR, merge, or publication occurred.

- Candidate: `hw-results/baseline-candidate.json`
- Exact candidate SHA-256: `08c1b1a13522301b2e5ef714971bd5194fc62607c9b25e96e753d71ebe087b28`
- Candidate mode/size observed: `0600`, `76373` bytes
- Eligibility: `eligible=true`, empty `reasons`
- Production baseline version: `2026-08-26.1`
- Approved source commit: `41d3236a5f5f3b8168deeef7fa9ed73d8de0339a`
- Fixed identity: `max9296.module_sha256=138d641870a8063f9f8682ba500e1bc6f509c284bc3e06bc37f751a49615818c`
- Comparability: board `pim`, target `192.168.214.4`, fixture
  `multi_1ch_0_720p`, encoder `h265`

## RED/GREEN evidence

Added `test_committed_production_baseline_recomputes_the_reviewed_evidence_as_pass`
before creating `baselines/hw-baseline.json`. It loads the real production
baseline, validates the real PASS fixture, and recomputes the verdict through
the real evaluator.

- RED: `rtk pytest tests/test_hw_gate_baseline.py -q` -> 22 passed, 1 failed.
  The test failed at `load_baseline(PRODUCTION_BASELINE)` because
  `baselines/hw-baseline.json` did not exist, the intended missing-production
  behavior.
- GREEN: after the approved baseline and fixture were added (and a missing
  `validate_structure` test import was corrected), the same command ->
  23 passed.

## Promoted data and fixture

- Added `baselines/hw-baseline.json` manually with `apply_patch` from the
  candidate's nested `baseline` object. It retains all calibration samples,
  source run IDs, median references, tolerances, units, and A-D mixed-combo
  expectations. The sole semantic difference is `source_commit`, changed from
  forty zeroes to the approved collection HEAD.
- Updated `tests/fixtures/hw_gate/evidence_pass.json` to bind the exact
  production-baseline SHA-256
  `b4c6a7ec343f2dbf4663e3f2571e41b0a5bc13012a161302fe184a70aa84a7a9`,
  approved source commit, wired target identity/comparability, all eight BPS
  metrics, and all 38 mixed-combo metrics.
- Updated `tests/test_hw_gate_evidence.py` only because its synthetic baseline
  helper intentionally derived one gate and one metric from the old compact
  fixture. The complete fixture caused its noncanonical baseline comparison to
  return `ERROR`. An in-memory all-gate comparison reproduced `PASS`; the
  helper now derives every fixture gate and metric, preserving the existing
  recomputation assertions.

## Validation evidence

- `rtk python3 -c '...'` semantic comparison -> PASS: production data equals
  `candidate["baseline"]` after replacing only `source_commit` with the
  approved HEAD.
- `rtk python3 -m hw_gate validate --evidence
  tests/fixtures/hw_gate/evidence_pass.json --baseline baselines/hw-baseline.json`
  -> exit 0.
- `rtk pytest tests/test_hw_gate_evidence.py tests/test_hw_gate_baseline.py -q`
  -> 48 passed.
- First Task 8 matrix run -> 312 passed, 6 failed, all in
  `tests/test_hw_gate_evidence.py` due the obsolete single-gate helper above.
- In-memory hypothesis check for a complete synthetic all-gate baseline ->
  PASS.
- Corrected Task 8 matrix:
  `rtk pytest tests/test_hw_gate_*.py tests/test_checks_bps_evidence.py
  tests/test_checks_mixed_combo_evidence.py tests/test_checks_target_identity.py -q`
  -> 318 passed.
- Full repository `rtk pytest` -> 2321 passed.
- `rtk ruff check tests/test_hw_gate_baseline.py tests/test_hw_gate_evidence.py`
  -> no findings.
- `rtk git diff --check` -> clean after intent-to-add includes the new baseline
  and this report.

## Self-review

Checked the complete diff against the promotion brief:

- Candidate data are semantically preserved except for the approved commit
  binding.
- BPS calibration samples, 5% median tolerance, 10% target tolerance, and all
  mixed-combo exact values remain unchanged.
- The fixture has complete BPS and mixed-combo inventories with matching units,
  baseline values, and reviewed rules.
- Runtime modules, legacy WiFi defaults, and ETH0 mutation paths are untouched.
- No hardware or external-state command was invoked.

## Concerns

None. The one integration issue found by the required matrix was a test helper
coupled to the prior intentionally incomplete fixture; it was corrected and
the focused, matrix, and full suites pass.

## Fix round 1/5: production-fixture binding and BPS prerequisites

Addressed the two review findings without changing production runtime behavior,
calibration data, thresholds, target identity, or mixed-combo expectations.

### Changes

- `tests/test_hw_gate_baseline.py` now calls the real `hw_gate.cli.main()`
  `validate` command with the committed evidence and baseline paths, asserting
  exit `0`. This specifically exercises the CLI binding check for both
  `baseline.sha256` and `baseline.source_commit`, which is intentionally beyond
  `validate_structure()` and `recompute_overall_verdict()`.
- `tests/fixtures/hw_gate/evidence_pass.json` now retains all controlled BPS
  encoder prerequisites: `ch0.qp_min=[0,0]`, `ch0.qp_max=[0,0]`, and
  `ch0.quant=[-1,-1]`, each with identical observed values and `PASS` verdict.

### TDD and validation evidence

- Test correction pre-data run:
  `rtk pytest tests/test_hw_gate_baseline.py::test_committed_production_baseline_recomputes_the_reviewed_evidence_as_pass -q`
  -> 1 passed. This legitimately passed before the fixture-only addition
  because the production CLI binding enforcement already existed; the review
  finding was that the previous test did not invoke that boundary, not that the
  runtime implementation was missing.
- Focused amended coverage:
  `rtk pytest tests/test_hw_gate_baseline.py tests/test_hw_gate_evidence.py tests/test_hw_gate_adapter_bps.py -q`
  -> 64 passed.
- Real command:
  `rtk python3 -m hw_gate validate --evidence tests/fixtures/hw_gate/evidence_pass.json --baseline baselines/hw-baseline.json`
  -> exit 0.
- `rtk ruff check tests/test_hw_gate_baseline.py` -> no findings.
- `rtk git diff --check` -> clean.

### Fix-round self-review and concerns

The test invokes the real CLI command handler, so a changed fixture digest or
source commit now makes this test fail through the production binding gate. The
fixture values match `profiles/cases/multi_1ch_0_720p.yaml` exactly. No runtime
or policy file changed, and no hardware or external-state command was invoked.
There are no remaining concerns.
