# PIM Durable Failover Simulation Implementation Plan

> For agentic workers: use test-driven-development and verification-before-completion skills. Follow checkable steps in this plan.

**Goal:** Exercise the reviewed owner intent journal across actual controller process termination and a separate local process recovery, without reaching the PIM board.

**Architecture:** Use an offline-only fixed-name simulated device with durable local files and the previously reviewed JournalStore at PR184. A simulation-only coordinator writes mutation intent before a local artificial fault, then reliably resumes idempotent restoration from the saved journal. The controller cannot authorize any real-world action.

**Tech Stack:** Python stdlib; Linux file persistence, pytest and test-only subprocess abrupt exit.

**Spec:** docs/qa-durable-journal.md and prior shared handoff pim-check/wsl/20261030T053057Z-durable-journal-pr184-ci-claude-final.md.

## Constraints
- Work only in the isolated WSL worktree: /home/jhw/projects/.worktrees/pim-check-durable-failover-20261030; parent commit e0a8fad156e21f80b4140e565165e142ac796308.
- Preserve original main HEAD9706005 and the user's dirty AGENTS.md.
- No board SSH, I2C, ORD, VCM, camera recordings, systemd installation, SD cleanup, OHT COPY or fault campaigns.
- Every result returns decision BLOCK, execution_authorized false. A successful simulation proves only local replay semantics.
- Do not change reviewed PR184; create a new stacked Draft PR.

## Review Focus
1. Process crash after simulated mutation, before return: new process must detect MUTATION_PENDING and restore.
2. Crash after restore but before final journal update: restarting must rerun restoration idempotently.
3. Failed restore and mismatched/corrupt baseline: preserve unresolved journal state and fail closed.
4. Wrong Git source pin, concurrent run or missing journal: reject before new effects.
5. Unexpected mutation while PREPARED: reject rather than misclassify as safely aborted.

## Task 1 - RED and GREEN
**Files:** Create scripts/qa_durable_failover_sim.py, tests/test_qa_durable_failover_sim.py.
- [ ] Write actual temp-file and subprocess os._exit tests before production code.
- [ ] Observe expected RED for missing module.
- [ ] Implement local-only device and coordinator using reviewed JournalStore transitions. Mutation intent must be durable before the simulated effect.
- [ ] Confirm all focused tests and Ruff GREEN.

## Task 2 - Fail-closed recovery
**Files:** Add narrow edge tests and minimal corrections.
- [ ] Verify interrupted restoration, wrong source, corrupt baseline, duplicate campaign and operator final-approval boundary.
- [ ] Require no network or board imports in the implementation; no automatic COMPLETE or release claim.
- [ ] Confirm original WIP untouched.

## Task 3 - Delivery
**Files:** docs/qa-durable-failover-sim.md, this plan.
- [ ] Run entire pytest --cov -q -ra, setup.py coverage 90 percent gate, Ruff, git diff --check.
- [ ] Deliver exact tested blobs to a new Draft PR stacked onto PR184; no merge or deployment.
- [ ] Verify CI and authentic Claude/Gemini reviews and leave Draft.
- [ ] Write timestamped handoff; update WSL/buildserver progress.
