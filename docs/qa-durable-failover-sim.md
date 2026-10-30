# Offline PIM durable failover simulator

The module scripts/qa_durable_failover_sim.py is a controlled, **local-only**
replay harness for the intent journal from Draft PR #184. It is NOT an
owner-side persistent QA service, PIM board executor, recovery daemon, or
a general callback adapter.

## Safety boundary

- The simulated target must be in a directory named exactly
  simulated-device under the OS temporary directory. It cannot be pointed
  at /mnt/sd_cam, an actual target mount, or the source checkout.
- It writes only the local fixture files baseline.bin and current.bin.
  Each is restricted to 4 KiB and mode 0600; the fixture directory must
  be mode 0700, not a symlink. Temp writes are fsynced before atomic
  replacement and parent directory fsync.
- There is no SSH, board RPC, product runtime import, arbitrary command,
  real long lease, systemd unit, camera, OHT COPY, SD recycle cleanup,
  reboot, or permission to start a campaign.
- All JournalStore results keep execution_authorized=false and
  decision=BLOCK. Even the final simulated RESTORE result requires
  independent hardware final attestation.

## Durable transitions under process death

- prepare(run_id): create an immutable, source/fixture baseline-pinned
  PREPARED journal only when current local fixture still equals baseline.
- simulate_mutation(): record BASELINE_SAVED and fsync MUTATION_PENDING
  before changing the simulated fixture to the artificial fault marker.
- recover() from PREPARED or BASELINE_SAVED: only report
  ABORT_WITHOUT_MUTATION when the local state is actually untouched.
  Unexpected unjournaled changes are blocked.
- recover() from MUTATION_PENDING: persist RESTORE_PENDING before
  performing the local restore. If restore fails, the journal stays
  RESTORE_PENDING so a new process can safely retry.
- recover() from RESTORE_PENDING: redo the local restoration idempotently.
  After checking actual restored fixture bytes, persist
  FINAL_ATTESTATION_PENDING.
- recover() from FINAL_ATTESTATION_PENDING: check fixture bytes again;
  still return INDEPENDENT_FINAL_ATTESTATION_REQUIRED, not approval.

The journal SHA digest detects accidental corruption, not malicious
tampering by someone with write access to owner-host files.

## Offline test evidence

tests/test_qa_durable_failover_sim.py invokes a genuine Python child
process which fsyncs MUTATION_PENDING, modifies only a temporary fake
device, then sends itself SIGKILL. A *new* Python process reopens the
original journal and replays restoration; pytest verifies original
fixture bytes, journal revisions, and no execution authorization.

Other tests cover controller termination after restore but before final
state update, failed restore and subsequent retry, unsuccessful mutation
after saved intent, wrong source pin, changed baseline, nonexistent or
corrupt journal, duplicate campaign, unexpectedly modified fixture
without intent, symlinked baseline, and forbidden actual-board path.

This demonstrates **local process-restart recovery** on the same
filesystem. It does not simulate loss of the entire host or power,
actual PIM board lease ownership, ORD/VCM target state, writer EOS/fsync,
or 4-channel camera throughput. An independent registered and approved
owner-host service must eventually supply those effects and their
recovery after host restart.

## Deployment and QA admission

The current ChatGPT host execution limit remains 1,800 seconds.
A future real 45-minute QA requires 2,700 seconds of campaign,
1,860 seconds reserved for restore, and an additional 60 seconds for
the exclusive board lease release. Do not split a fault campaign into
untracked detached shell commands or assume that this simulation
authorizes a real board mutation.

The existing reviewed PR #184 and the original WSL main source remain
untouched. This simulator is delivered only in a separate stacked Draft
PR to preserve the completed Claude/Gemini review for PR #184.
