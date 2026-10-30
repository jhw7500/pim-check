# Durable PIM QA journal offline foundation — implementation plan

For agentic workers: follow test-driven-development and verification-before-completion.

Goal: fail-closed, append-only durable audit trail and admission evaluator, NOT a hardware runner.
Architecture: Python stdlib module, Linux O_NOFOLLOW + flock, fsynced hash-chain JSONL, explicit state transition graph, offline pytest.
Spec: docs/superpowers/specs/2026-10-30-durable-pim-journal.md.

## Constraints
1. Original WSL project checkout and AGENTS.md are user-owned; preserve.
2. Avoid changes to scripts/qa_acceptance_safety.py, with_pim_board.sh or run_with_deadline.py.
3. Do not create background processes, live leases, deployed systemd services, OHT commands or board mutations.
4. Keep PR181 Draft/unmerged; publish narrow separate Draft PR after tests.

## Review focus
- Crash after durable RESTORE_ARMED but before external apply: recovery pending.
- Partial JSONL, tampering, invalid record hash: fail closed, no repair.
- Concurrent writer and symlink/hardlink/weak mode: refuse.
- Incomplete restore or wrong baseline hash: cannot close.
- 1800s host limit vs 2700s campaign + 1860s cleanup: admission stays blocked.

## Task 1: journal and state machine
- [ ] Add tests in tests/test_qa_durable_campaign_journal.py for contract, private storage, SHA256 chain, lock, allowed/forbidden transitions and reopen after crash.
- [ ] Run focused pytest, confirm RED.
- [ ] Implement scripts/qa_durable_campaign_journal.py only; no board API.
- [ ] Rerun focused pytest GREEN and Ruff.

## Task 2: admission
- [ ] Tests for pending runs, corrupted journals, closed review-only result and timeout 1800s.
- [ ] Confirm RED, implement minimal safe inspection.
- [ ] Confirm focused GREEN.

## Task 3: verification and delivery
- [ ] Write docs/qa-durable-campaign-journal.md about limitations, owner service prerequisites and recovery journal semantics.
- [ ] Run focused + entire pytest with coverage, setup coverage gate, Ruff and diff checks.
- [ ] Create separate narrow Draft PR; verify new SHA and GitHub CI; never merge or deploy.
- [ ] Share evidence/next steps in project-handoffs.
