# PIM recovery adapter — owner review and activation contract

**Status: source/test candidate only. No actual physical restoration, systemd activation, host-command registration or board configuration change.**

## Implemented source boundaries

- `scripts/qa_owner_board_inspector.py`: one fixed read-only probe over an existing SSH client under the official PIM lease. Reads source/ORD/runtime SHA, on-board strict journal, installed gstApp/ord/vcm and Finalizer hashes, owner status, camera heartbeat, health and writable media FD state. Unexpected symlink/UID/mode, unreadable FD, malformed source, journal or binary metadata: fail closed.
- `scripts/qa_owner_board_adapter.py`: `OfficialLeaseVerifier` checks clean pinned checkout and the real `with_pim_board.sh --check-held --until <UTC> --long-lease true --purpose <fixed>`. The wrapper checks owner ancestry, exclusive alive jhw-control session, deadline supervisor; environmental markers alone never grant authority. Lease is reverified after board observation.
- `PinnedBoardRecoveryAdapter`: exact immutable baseline source/config hashes and installed manifest; only permitted external restore API is existing `hw_gate.transaction.recover_pending_transaction()`. No generic command, ad-hoc reboot, SD deletion, fault injection or 4ch setup API. On-board journal must be exact same run/original hash and one of JOURNALED/APPLIED/REBOOTED to begin; RESTORED/VERIFIED/CLOSED require separate review, not another blind reboot.
- `scripts/qa_owner_recovery_controller.py` from PR190: fsync-backed host RESTORE_PENDING intent, whole-process owner_lock, post-restoration source/ORD/runtime/LED/consumer proof, then FINAL_ATTESTATION_PENDING with execution_authorized=false.

## Verified offline test scope

- Official long-lease verifier rejects missing/spoofed marker, short/expired lease, changed owner/session, and official check failure.
- Typed board witness blocks mismatched installed binary/source hash, foreign or stale journal, LED ON, busy owner, live writer; repeated restoration in the same owner instance is refused.
- Real OS SIGKILL is sent to a subprocess after it durably updates a **temporary fake-board file**. On a separate child process restart, the existing host journal advances to independent final attestation without running a second fake recovery. This is NOT a systemd cgroup test and NOT physical board recovery.
- Remote collector Python source is syntax-checked and its typed results and fail-closed behavior are tested with fake SSH. There has not yet been a physical-board run of this new collector.

## Missing acceptance gates (do not reinterpret as grants)

1. Owner must review and install a source-pinned long-lived systemd service and exact host-owned executable, then register an honest external_call profile in private mode0600 `host-commands.json`. A general host grant or a nonexistent profile ID cannot substitute for the exact one-use approval card.
2. The physical `PimReadOnlyInspector` must be separately validated under the official exclusive board lease. No restoration method in that read-only session.
3. Installed ORD/VCM/gstApp/Finalizer source identity, owner transition readiness, exact root-owned on-board recovery journal, and closed writer FD/EOS must be independently verified. The older installed Finalizer does not prove PR229 safeguards.
4. Quiescing active gstApp writers is a separate owner-approved action; the adapter intentionally does not stop them itself. If writers are still open, restoration remains BLOCK.
5. Exact LED-OFF original baseline on MCP4018-absent target 192.168.214.4 must survive both restart and recovery. The historical desired source SHA256 is `1e26aa8262257b0e4b75efae73e2da735d53d2ce3e5974c49419818838f087f7`; remeasure on target before any write.
6. Host source pinning and real systemd SIGKILL/restart/network interruption/board boot/restore require a separate controlled maintenance acceptance. StrictHardwareTransaction restores edgeconf; unchanged ORD and merged runtime must be separately attested.
7. The full 45-minute campaign requires roughly 4620s supervised duration. Dynamic host exec 1800s and fixed command 3600s alone do not satisfy this. SD retention/eligible natural prune is also independently pending.

**No board writes or authorization configuration changes are authorized by this document.**
