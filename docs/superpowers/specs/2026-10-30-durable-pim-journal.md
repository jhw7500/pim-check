# Durable PIM QA recovery journal: offline foundation (2026-10-30)

## Scope and boundary
Implement an OFFLINE crash-recovery journal and admission evaluator for a future owner-controlled 45-minute i.MX8MP QA campaign. This does not install a persistent service, acquire a board lease, run plans or issue target SSH, reboot, or delete files. The pinned no-delete safety gate stays unchanged.

## Contract
- Bind run_id, Git 40hex SHA, exact board pim and 192.168.214.4, a prior baseline 64hex SHA256 and an owner-plan identifier.
- Campaign exactly 2700s, restore reserve at least 1860s, exclusive lease spans both plus 60s. Host caller 1800s is insufficient to run unattended.
- Private 0700 journal parent, regular 0600 link-count-1 file, no symlinks, one nonblocking exclusive file lock, O_NOFOLLOW and CLOEXEC. Append-only fsynced JSONL with strictly verified hash chain; fsync the parent after creation.
- State machine PREPARED -> BASELINED -> LEASED -> RESTORE_ARMED -> RUNNING -> RESTORING -> RESTORE_CHECKED -> CLOSED. Allow RESTORING from RESTORE_ARMED for prelaunch recovery. FAULT_UNKNOWN blocks further mutation until explicitly controlled recovery.
- The RESTORE_ARMED event is durable BEFORE any future controller mutates the target. An incomplete/corrupted journal blocks admission, never silently truncates or assumes recovery.
- Restore proof requires a matching baseline digest and separate receipt SHA256. Hashes in this library are caller-supplied evidence claims, NOT cryptographic owner authorization.
- Any non-CLOSED run blocks new campaigns. A CLOSED run only returns REVIEW_REQUIRED, execution_authorized=false, since source/writer/lease/recovery assurances are external.
- No shell, network, deletion, reboot, external subprocess or board command API here.

## Separate, future, owner-approval phase
An owner-installed persistent service must attest source, approved command profile, backup/restore and FD/EOS; hold one official PIM lease; supervise plan and recovery with 2700s+1860s; survive UI/process/host restarts; and verify final state before enabling a real campaign. Direct detached background commands or unbounded retries do not satisfy this.

References: PR181 head 4dacb9b92509442cd0c2df26a26ac2be2c5ce950; shared handoff 20261030T033035Z-managed-writer-durable-runner-handoff.md.
