# ORD-managed storage: offline acceptance proposal

This proposal does NOT change the canonical, source-pinned,
scripts/qa_acceptance_safety.py --live admission gate. It is not a board
runner, a lease, or an instruction to delete recordings.

The existing safety gate requires 45 minutes of four-channel recording
headroom without relying on automatic cleanup. That is conservative but
does not fully represent normal ORD production retention at a 90% volume
threshold and a 1,000-root-file limit.

The pure module scripts/qa_managed_retention_policy.py estimates:
- Expected newly recorded KiB and root-file entries for the campaign.
  Budget two entries (MP4 and SRT) per channel per recording segment
  when SRT disablement has not been independently attested.
- Needed root-file rotations, counting files (not only video segments),
  with 100-file reserve.
- Needed verified recycle reclamation before the configured 90% limit,
  with an additional 512MiB size reserve.
- Possible rotation work from the proven cleanup interval and bounded
  files per pass.

Evidence requirements are explicit: source-to-installed build identity,
merged runtime, owner lease, mount paths, writer FD/EOS completion
contract, readable proc FD scope, scoped deletion semantics, actual
observed cleanup, per-campaign authorization, fresh measurement of
ELIGIBLE closed root recordings and reclaimable recycle bytes, and a
durable 45-minute campaign/restore runner.

A recycle directory with 195GiB of files is not proof that 195GiB is
safe to delete. Observed cleanup logs are not proof of the installed
PR #228 source or race-free writer closure. Current 0.6.3.2 installed
baseline lacks source/build attestation for ORD PR #228, Finalizer #229,
and gstApp #150. The official board was previously measured with SD
near 90% and open gstApp .part O_WRONLY FD. This is normal for an active
recording, but writer closure for candidate FINAL files is unproven.

When any evidence is missing, decision is BLOCK. Even a complete
synthetic evidence bundle returns REVIEW_REQUIRED with
execution_authorized=false. No code in the proposal opens SSH, sends
COPY, acquires a board lease, reboots, changes settings, or removes files.

The previous pinned read-only live safety worktree must remain clean
and unchanged. To deploy a managed mode, independently review the
new policy, audit the installed source and writer-completion protocol,
pin a separate safe source commit, verify cleanup and restore
scheduling, and request explicit owner-approved campaign admission.


## MP4 + SRT file-count correction

The observed board root contained 467 MP4 files and 467 SRT files, and
VCM had `srt_enable=true`. For a 45-minute / four-channel campaign of
one-minute segments, the conservative projection is **360 root file
entries** (180 MP4 + 180 SRT), not only 180 video segments. From a
934-file root snapshot, this proposal requires 394 eligible closed-file
rotations to retain the 100-file reserve. These are planning figures,
not a live deletion count or permission to clean storage.
The legacy source-pinned no-delete gate counts 180 video segments in
its file projection; that alone cannot prove MP4+SRT headroom.
The canonical gate remains unchanged and BLOCKED for the actual board.
