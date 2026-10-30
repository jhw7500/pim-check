# PIM 45-minute QA durable journal: offline component

## Scope
The offline standard-library module scripts/qa_durable_campaign_journal.py
creates an immutable-identity, append-only event journal for a future,
independently approved, persistent owner-controlled QA service.

It **does not** start a campaign, acquire board leases, read hardware,
send OHT requests, mutate configuration, delete recordings, reboot,
create systemd services, or authorize any execution. It must NOT be
considered an installed owner runner. The pinned no-delete live safety
gate and existing PR181 managed-retention evidence policy remain intact.

## Identity and storage
- Exact target: board_id pim, 192.168.214.4.
- Versioned source Git SHA40 and original baseline SHA256, run ID32hex,
  owner-plan identifier, campaign 2700 seconds, recovery reserve >=1860
  seconds, lease >= sum of both plus 60 seconds.
- Owner-private journal directory: mode0700, owner UID. Each per-run
  JSONL file mode0600, single hardlink, O_NOFOLLOW, nonblocking exclusive
  flock for one writer. A symlink/hardlink, wrong mode, missing file,
  inaccessible directory or unknown directory entry BLOCKS admission.
- Each transition adds one SHA256-chained JSON event, with sequence,
  previous SHA, UTC timestamp, phase, and opaque proof SHA. Data and
  creation directory are fsynced. Unfinished/torn/tampered entries cause
  CORRUPT_JOURNAL; nothing is automatically truncated or repaired.
- Hashes detect accidental corruption; they are NOT digital signatures.
  A caller-provided receipt can be forged. An owner-trusted verifier is
  required for source identity, baseline and restore proof.

## State semantics
PREPARED: journal created; NO board changes authorized.
BASELINED: baseline snapshot digest claimed.
LEASED: lease identity proof digest claimed (not authoritative).
RESTORE_ARMED: persist this event BEFORE any separate controller may
attempt a target mutation. Treat all later abnormal termination as a
pending restoration.
RUNNING: campaign began according to external controller evidence.
RESTORING: controller began verified restoration.
RESTORE_CHECKED: reported final hash matches original baseline AND a
separate proof receipt is present. NOT a trusted independent audit.
CLOSED: reported process completed; only REVIEW_REQUIRED, never a
release approval or verified camera restore claim.
FAULT_UNKNOWN: outcome ambiguous, remains recovery pending. May only
enter an explicitly recorded RESTORING path, not directly CLOSED.

On restart, any non-CLOSED run (even PREPARED/BASELINED/LEASED) blocks
a new campaign. RESTORE_ARMED/RUNNING/RESTORING/RESTORE_CHECKED/
FAULT_UNKNOWN report requires_restore=True. A crash after RESTORE_ARMED
but before any actual target mutation yields a conservative false
positive (recovery investigation), never a false success.

## Durability and process crash tests
- Real child os._exit without Python finalizers after RESTORE_ARMED.
  Next process reopened the same journal, found pending restore and
  refused CLOSED until RESTORING/RESTORE_CHECKED path.
- Concurrent owner open blocked by flock; unlocked journal was safely
  reopened, corrupt hash/nonfinite JSON, partial JSONL, bad transitions,
  changed inode/path/mode, and forged baseline were rejected.
- No test depends on access to hardware or configuration secrets.

## Why 45-minute QA is not yet permitted
The chat host execution has a maximum single call duration of 1800s;
planned recording is 2700s and cleanup/restore reserve 1860s. Existing
scripts/with_pim_board.sh and scripts/run_with_deadline.py only guard
their currently alive parent process group. They do NOT maintain an
independent restart-persistent restoration service when that process
disappears. Merely requesting a 90-minute board lease or using nohup
does not meet the controlled QA requirement.

The owner still needs to register and independently test an immutable
approved host-command profile and persistent supervisor: sealed source
and baseline, exact target & scopes, official exclusive lease owner
fencing, recovery journal, restore-on-controller-loss, heartbeat and
deadline, SIGTERM/KILL escalation, restart/recovery idempotence,
known writer EOS/FD close, SD remount/source/clock-change blocking,
final configuration checksum, and lease release proof.

The current module's result ALWAYS contains execution_authorized=false
even if all journal records are syntactically valid and CLOSED. It
cannot install the missing long-running service or prove that the
board was ever restored. Owner-specific authorization and real board
mutation remain strictly separate steps.

## Current source and ownership
- New feature is developed in an isolated worktree based on
  pim-check PR181 exact SHA4dacb9b92509442cd0c2df26a26ac2be2c5ce950.
- Do not modify original WSL checkout AGENTS.md or existing feature
  branch, previous pinned safety checkout, ORD/VCM/gstApp binaries.
- Deliver as a separate narrow Draft PR to avoid rewriting the
  approved PR181 scope. No merge, deployment or automatic execution.
