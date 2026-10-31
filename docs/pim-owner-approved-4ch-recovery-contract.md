# PIM owner-approved bounded 4ch and durable restore contract

Status: **engineering candidate only**. No host command profile, systemd
unit, hardware-recovery adapter, or physical board action is installed or
authorized by this document or its accompanying offline code.

## Existing evidence (2026-10-31, not current admission)

- Target: i.MX8MP 192.168.214.4, MCP4018 physically absent.
- Desired **LED-OFF** source edgeconf SHA256:
  1e26aa8262257b0e4b75efae73e2da735d53d2ce3e5974c49419818838f087f7.
  The earlier LED-ON backup is **not** the desired post-test baseline.
- Running config at previous inspection: 1920x1080 / 15fps / ch0 only;
  all led_flash.enable values false, VCM SRT enabled.
- Source for 4ch test: PR188 no-MCP profile. Capacity guard PR189
  always has execution_authorized=false.
- Last observed physical storage (2026-10-31T07:11:38Z):
  used 208276120KiB, free 24083904KiB, root files **982/1000**;
  recycle 204527720KiB. No verified recycler prune in this window.
  Policy 90% leaves 847901KiB; reserve 512MiB leaves just
  323613KiB for estimated new recording *without a verified reclaim*.
  All values expire for execution admission; a fresh read is mandatory.

## Shorter four-channel test proposal (not hardware-approved)

The original 4ch case at 15360kbps includes stabilization200s,
monitor300s and restore allowance300s = 800s. Budget:
1500000KiB video, conservative 112 root entries (4 MP4 + 4 SRT per
60s segment), and 111 closed-file root rotations needed with the above
982-root snapshot and a reserve of16 root entries.

Two modeled lower-rate possibilities, **not hardware-validated**:

| mode | rate | stabilize+observe+restore | video budget | files budget | minimum root rotations |
| --- | --- | --- | --- | --- | --- |
| rate-only | 4x512kbps | 200+300+300=800s | 200000KiB | 112 | 111 |
| shorter | 4x512kbps | 200+120+300=620s | 155000KiB | 88 | 87 |

This keeps the 512MiB reserve within the **historical** 90%-limit
headroom, but does not solve the file-count limit. For the 620s case,
root files must be at most **895** to fit 88 projected entries plus
16 reserved slots without any further rotation. Even a mathematical
single-segment scenario creates at least8 file entries, so the
observed 982-root state needs >=7 valid rotations with a 16-file reserve.
For two finalized segments it needs >=15 rotations (root<=967).
A full independent 4ch functional test may need more, because capture
startup and two completed finalizations consume real time.

Existing 4ch BPS quality tests expect their configured 1024/2048/
4096/8192kbps values. An alternative 4x512kbps profile would be a
separate **basic channel-continuity** test, not full bitrate acceptance.
It must use a new profile and independent tests; do not mutate PR188
in place or assert that 512kbps is supported before testing.
Any projected recycling must be supported by installed ORD source,
eligible closed-session/writer FD proof, actual measured rotation/prune
capacity and verified finalizer lifecycle. Raw recycle directory size
is **not** safely reclaimable capacity.

## Official registered host-command owner path (not installed)

The buildserver general host grant is separate from the one-use
fixed-command approval. The connected buildserver implementation is
personal-ops/chatgpt-mcp/host_command_{profiles,tools,operations}.py.
At the last query, list_host_command_profiles(pim-check) reported **[]**.

An OWNER-REVIEWED, executable, pinned outside any registered repository
is needed before attempting registration. The owner-only config is
~/.config/chatgpt-coding-mcp/host-commands.json, a regular file owned
by the service account and mode0600, outside repository write scope.
Each named profile contains exactly project, label, executable,
executable_sha256, argv, cwd, timeout_seconds, effect, inputs.
Actual buildserver accepts effect external_call for remote effects;
**never misclassify board mutation as read_only**. Effect enumeration
does not itself authorize an operation.

The owner must review and install the executable, pin hashes of every
relevant source input and executable, and ensure:
1. Source checkout identity and hardware target 192.168.214.4 exact.
2. An independently acquired official exclusive jhw-control PIM lease,
   with sufficient *remaining* lifetime and fenced owner identity.
3. Fresh read-only storage/retention gate. If BLOCK, no setup or writer
   changes; no manual deletion to force a PASS.
4. An on-board and host-side durable snapshot of the LED-OFF baseline,
   including edgeconf, ord_vcm, runtime and installed binary provenance.
5. The restorer must survive an MCP process disconnect and interrupt,
   and must operate only under the verified owner lease.
6. The exact restored edgeconf SHA, unchanged ORD SHA and runtime SHA,
   active ch0 only, LED all OFF, camera/ORD/VCM readiness and absence of
   dirty journals must be independently confirmed.
7. Partial/unknown outcomes remain RESTORE_PENDING or
   FINAL_ATTESTATION_PENDING, never silently PASS.

Then, and only when the registered profile appears in
list_host_command_profiles, call prepare_host_command with the actual
profile_id, open_host_command_approval using the returned operation_id,
let the user explicitly confirm that single plan, execute_host_command,
and verify its final operation plus independent PIM board status.
No ChatGPT action can install this owner's private policy file itself.
Repeated general host approvals do **not** register this profile.
Changing a previously denied command's executable, project or argv
to avoid safety enforcement is NOT this workflow.

## Recovery reconciliation in the new offline module

scripts/qa_owner_recovery_controller.py provides a trust-checked
state-machine layer over the existing scripts.qa_durable_journal.JournalStore.
It is not a board adapter and does not implement SSH, jhw-control, camera
reset or install a systemd unit. Tests use a FakeAdapter only.

- Host journal PREPARED / BASELINE_SAVED: no hardware contact.
- MUTATION_PENDING: atomically persist RESTORE_PENDING before attempting
  board inspection or restoration.
- RESTORE_PENDING + on-board DIRTY journal for the same run and exact
  original hash + fresh official lease proof: invoke only a separately
  reviewed adapter's recover_validated_board_journal operation.
- RESTORE_PENDING + on-board journal ABSENT and independent exact
  edgeconf/ORD/runtime/LED/consumer proof: advance without replaying
  reset. This is essential for a crash after the remote operation
  completed but before the host acknowledged it.
- Journal missing, corrupt, foreign, no board journal with mismatched
  config, expired lease, source drift, changed owner, unclosed writer,
  missing consumer, or wrong original digest: **block**.
- Recovery source and observer share the existing exclusive owner_lock,
  held across *all* adapter calls, preventing concurrent recovery.
- Exact verified postconditions advance to FINAL_ATTESTATION_PENDING;
  an independent reviewer/service must still finish final acceptance.
  Every status maintains execution_authorized=false.

The owner_lock exception handling was corrected so an InterruptedError
thrown by a recovery adapter is not transformed into a false lock error;
it is important that the durable journal remains RESTORE_PENDING.

## Proposed systemd runtime design (NOT installed)

This is a deployment contract, not an executable unit yet. The
pre-existing deploy/pim-qa-owner-observer.service is deliberately
PrivateNetwork=yes and cannot restore physical board state.

A reviewed replacement service should have:
- A sealed/pinned executable, run as the owner under systemd with
  linger enabled and no reliance on the chat session.
- Type=notify + WatchdogSec with verified sd_notify, StateDirectory
  mode0700 and fsync'd journal writes.
- Restart=on-failure/RestartSec, strict filesystem protections, limited
  network access to exactly the PIM target and the owner control plane.
- Its own persistent 90-minute or longer campaign/restore supervision:
  45min=2700s + restoration grace1800s + release margin60s = 4560s,
  with the existing acceptance gate requiring a further 60s margin
  (the project currently models **4620s** overall). Neither dynamic
  host execution max1800s nor fixed command max3600s guarantees this.
- Official jhw-control board with --long-lease true fencing and
  acquisition/reacquisition on restart; do not read a bare environment
  flag as sufficient lease proof.
- No automatic new fault injection on restart. Recovery-only after
  a dirty journal, verified installed source and trustworthy board
  state; never reapply the target 4ch profile merely because a worker
  crashed.
- Fail-closed on loss of owner, lease, finalizer writer identity, time
  budget or postconditions. Never delete real camera data as a
  side effect of recovering.

Before creating or enabling such a service, an **actual** reviewed
BoardRecoveryAdapter must bind to the approved on-board
hw_gate.transaction.recover_pending_transaction path and prove
full camera/ORD/VCM/gstApp restoration and post-recovery safety.
No adapter, active service, systemd restart proof or 45min
hardware acceptance is claimed by this documentation.
