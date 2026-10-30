# PIM owner persistent observer — owner-controlled deployment contract

This module is a **real systemd-ready, restartable owner process** but **NOT a
hardware restoring runner yet**. It never contacts the PIM board or grants
permission to launch the 45-minute campaign.

## Verified local behavior

- One process per private 0700 user-owned state directory; nonblocking Linux
  flock under a 0600 regular single-linked NOFOLLOW file, validated inode.
- Uncommitted or SHA-mismatched source blocks any journal state transition.
- Restart reads the fsynced local journal. Only with a verified source pin
  can MUTATION_PENDING be advanced to RESTORE_PENDING.
- RESTORE_PENDING signals an unmet **real target restoration obligation**,
  not successful restoration. FINAL_ATTESTATION_PENDING also remains BLOCK.
- Atomic 0600 status JSON with file and directory fsync, and always
  execution_authorized=false, board_touched=false, real_restore_performed=false.
- Handles SIGTERM and SIGINT, emits sd_notify READY/WATCHDOG, and releases
  the OS lock when the process exits or is SIGKILLed.
- The systemd unit is version-controlled but **not installed or enabled**.
  PrivateNetwork=yes and no live board command protect the current boundary.

## Future installation, only with separate owner validation

The template `deploy/pim-qa-owner-observer.service` requires a trusted
`/etc/pim-check/owner-observer.env` containing an EXACT
`PIM_QA_SOURCE_SHA=<40-hex-installed-clean-checkout-head>`.
It runs under the buildserver `jhw` user with StateDirectory=pim-qa-owner.
Never use an unverified source tree or service configuration. Service status
being READY means *only* that the passive observer loop is running.

This is **not** a completion of the real 45-minute restore runner. Before
hardware mutation, separately implement and verify a fixed-command owner
worker with exactly pinned deployment artifacts, durable snapshots of ALL
affected camera, ORD, VCM and gstApp paths, lease ownership/fencing,
idempotent target restore and independent post-reboot SHA equality,
process-loss and host-reboot restart, watchdog and timeout budgets, 4-channel
writer EOS/FD closure and finalizer fsync, and scoped ORD rotation/pruning
provenance. The existing `hw_gate.transaction.recover_pending_transaction`
covers only its validated edgeconf transaction; it is not a complete
camera/ORD/VCM restore service.

Until these checks are independently proven, **no hardware lease, fault
injection, reboot, OHT COPY, recording deletion, or 45-minute campaign
may be initiated**. The previous host 1800-second cap remains shorter than
2700 seconds campaign + 1860 restore + 60 release, and a detached shell/
background host process is never a replacement for owner systemd supervision.
