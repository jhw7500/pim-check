# PIM offline durable journal core

This is a local-storage-only prerequisite for a future owner-approved 45-minute QA service. It is NOT an installed persistent supervisor and never contacts a board, copies recordings, reboots, deletes SD data, or authorizes a QA run.

## Persistence and ownership

The Python stdlib module scripts/qa_durable_journal.py accepts only a pre-existing owner-controlled local directory. It rejects a symlink or group/other-writable directory. A fixed lockfile and Linux flock serialize cooperating callers. Journal files are regular, single-link, mode 0600; read fails closed on missing, truncated or tampered records. Every record binds the exact PIM target address 192.168.214.4, full source Git SHA, run identifier, baseline SHA256 and revision.

Writes use a 0600 temporary file, fsync(file), atomic rename in the trusted directory and fsync(directory). A failed write before the rename does not destroy the previous valid record. Failure after rename is ambiguous and cannot be treated as approval. The self-checking SHA256 digest detects accidental corruption, not a privileged host attacker.

## Allowed intent flow

- PREPARED -> BASELINE_SAVED: no mutation was intended.
- BASELINE_SAVED -> MUTATION_PENDING: record this intent DURABLY before any target effects.
- MUTATION_PENDING -> RESTORE_PENDING: restart or failure requires restoration.
- RESTORE_PENDING -> FINAL_ATTESTATION_PENDING: never infer complete restoration from a process exit.
- FINAL_ATTESTATION_PENDING: independent owner/board final-state verification still required; no automatic completion transition.

Reopen during an unresolved intent gives RESTORE_REQUIRED. Every status from the journal is BLOCK with execution_authorized=false. The module has no board execution or restore function; those are future separately approved host-service responsibilities.

## Offline verification and remaining acceptance

Tests cover restart/resume, impossible state jump, concurrent stale revision, duplicate campaign, corrupt JSON/digest, wrong source/target, symlink attacks, owner-only permissions, failed atomic replace and fsync. All tests operate on temporary local storage, not on the PIM target.

A REAL owner-managed service remains to be implemented and independently tested: installed source pin, official long exclusive lease and loss fencing, persistent supervisor surviving caller loss, idempotent recovery on host reboot, actual writer EOS/FD closure and immutable final state, plus proof of lease release. The host single-execution limit 1800 seconds cannot cover 2700 seconds campaign + 1860 seconds restore; add a 60-second release margin. No untracked detached process or lease bypass is an acceptable substitute.
