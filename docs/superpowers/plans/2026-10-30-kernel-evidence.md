# Kernel Evidence Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make existing kernel failures actionable without weakening the verdict or accessing the board.
**Architecture:** Existing custom check plus opt-in pure evidence parser and bounded JSON projection.
**Tech Stack:** Python >=3.9, pytest, existing YAML profiles, POSIX shell/awk.
**Spec:** ../specs/2026-10-30-kernel-evidence-design.md

## Global Constraints
Keep expected zero, BSP exclusions, explicit session/boot-fallback distinction, and source kern.log. No live board work. Preserve AGENTS.md WIP.

## Review Focus
Missing boot identity must fail. Partial output must not imply complete events. Correlation is not root cause. Healthy process is not recovery. JSON must not leak arbitrary custom data.

## Task 1: Typed diagnostics and persistence
- [ ] Add failing tests in tests/test_checks_kernel_evidence.py, verify RED on current CustomCommandCheck/Reporter.
- [ ] Implement parse_kernel_evidence(output) and format_kernel_evidence(evidence) in checks/kernel_evidence.py.
- [ ] Attach opt-in evidence in checks/custom.py; preserve existing verdict and unrelated result shapes.
- [ ] Preserve the typed evidence in reporter.py JSON; verify tests GREEN.

## Task 2: Bounded source evidence
- [ ] Add shell regression cases for boot ID, complete raw samples, missing identity and read failure.
- [ ] Update the one board_error_detect command: bounded sample/metadata, source-read and boot-transition checks; preserve error count and exclusions.
- [ ] Update old shape/bounds tests to the executable contract, not broaden exclusions.

## Task 3: Verify and record
- [ ] Focused tests, full pytest, Ruff, diff checks and source/WIP hashes.
- [ ] Review boundaries; preserve FAIL for real or unavailable evidence.
- [ ] Record exact tested source and remaining live-board restriction in shared handoff.

## Execution ledger
Ruling: continue the already-selected feature branch in place with exact source/WIP guards; do not create a new workflow or disturb existing worktrees. Existing relevant baseline: 64 passed. No independent agent is authorized (allow_codex=false); review will be a documented direct review, not claimed independent.

Task 1/2: implementation and first focused GREEN 95/95. Extra boundary RED: 8 failed/35 passed; cause tokens were collapsed and reporter accepted unversioned/unrelated dictionaries. Corrected token preservation and field projection. Actual local read-failure/boot-transition tests already pass. No board work.

Direct final review (not an independent agent): found loss of the existing (got: None) infrastructure marker, an empty-after-precheck race, and unencodable text causing an exception. Added three RED cases and fixed each without changing the FAIL gate. First whole-suite run before these fixes: 3329 passed, 1 optional skip, 73 subtests. Final re-run and exact hashes will be in the QA manifest and shared handoff.
