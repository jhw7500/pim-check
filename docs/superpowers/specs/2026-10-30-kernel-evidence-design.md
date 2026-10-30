# Kernel error evidence design

## Intent
Continue the user's approved improvement of pim-check kernel error diagnostics, on WSL only. Existing source is 009123e. Never retry the previously blocked board collection or alter the target, source-pinned safety gate, driver, or retention policy.

## Approaches
A new globally registered check would change every snapshot's check count. Replacing the existing collector with journalctl would violate the observed source contract. Chosen implementation: retain the single board_error_detect custom item and its exact expected-zero gate; opt in to bounded, typed diagnostics on that item only.

## Contract
- Keep /var/log/cantops/kern.log and the existing BSP exclusion list and scope semantics. Emit exactly 0 only for a readable, valid, nonempty source with no matching errors. Source/read/boot-ID/time failures are nonzero evidence strings and FAIL.
- Error output includes count, observation boot ID, SESSION/BOOT_FALLBACK, start, first/last snippets, and at most 32 raw record prefixes of 512 bytes. Record omitted/clipped counts explicitly. The observation boot ID is not an independently proven per-line boot identity.
- Parse evidence in checks/kernel_evidence.py. CustomCommandCheck keeps the existing verdict; it attaches evidence only for evidence_format: kernel_log_v1. No other checks change behavior.
- Group only sampled lines sharing I2C adapter and channel within a two-second window from the first line. Groups are temporal/device candidates, not proven independent incidents or root causes. Never merge different devices. Exact duplicates have a separate sample-only count. Partial samples never claim complete grouping.
- Recovery remains UNVERIFIED: the source contains error records, not validated successful recovery transactions. Healthy gstApp, a later quiet interval, or grouping never downgrades FAIL to WARN/PASS. Unknown root causes remain UNKNOWN.
- JSON reports preserve only the typed kernel evidence, not generic custom command payloads or credentials. Text failure reasons include a bounded diagnostic summary.

## Constraints
No live SSH, board lease, deployment, reboot, hardware writes, additional backup, permission changes, or automatic log suppression. Preserve existing AGENTS.md user changes. Python >=3.9, existing dependencies only. Validate with real local shell fixtures, mocked SSH, full pytest, Ruff, and diff checks.

## Verification
Replay the existing four-line MCP4018 fixture. Check duplicate/device/window boundaries, missing identity, malformed/partial payload, source read failure, unsafe-control escaping, legacy custom-command compatibility, and JSON persistence. The physical cause of the board's historical MCP4018 failures is outside this change.
