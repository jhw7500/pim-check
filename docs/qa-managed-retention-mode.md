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

## 2026-10-30 real-board passive observation: root rotation vs. recycle prune

An owner-leased read-only observation on i.MX8MP 192.168.214.4 confirmed
normal ORD background rotation. Installed package was pim-mp
0.6.3.2+jhw.camera19; executable /usr/local/bin/ord had SHA256
f8c539067918edfb6393f89dea8cd0059f7e4abd964d56b2cba37685deae3d56.
Configured disk_manage=true, disk_limit_per=90, disk_limit_file=1000,
disk_manage_period=40, srt_enable=true.

| UTC | Root files | MP4 / SRT | Recycle directories | Recycle KiB |
| --- | ---: | ---: | ---: | ---: |
| 02:20:40 | 1,000 | 500 / 500 | 282 | 204,057,820 |
| 02:21:26 | 1,002 | 501 / 501 | 282 | 204,057,820 |
| 02:22:13 | 884 | 442 / 442 | 283 | 204,508,748 |

The 02:21:41 UTC ORD log reported file count 1002 > 1000 and an hourly
rotation command targeting recycle/20261029_18. The passive counters show
**118 net fewer root entries** (MP4 minus 59, SRT minus 59) and a
450,928 KiB growth in recycle. Since recording was active between readings,
these are NOT exact moved-file counts and do not establish that any specific
writer-closed file was safely selected. This is a corroborated rotation
signal, not a causal or safety attestation.

The final effective used fraction from used_kib/(used_kib+avail_kib)
was **89.47%**, whereas df displayed the rounded figure 90%. No live,
above-threshold recycle deletion and attributed free-byte recovery were
captured in this interval. Historic removal logs and absent older folders
are not proof of recovered bytes.

The policy now checks independent fields:
- automatic_root_rotation_observed, when projected root rotation is needed
- automatic_recycle_prune_observed, when projected recycle reclamation is needed

The old combined automatic_rotation_and_prune_observed flag is deprecated
and ignored as approval evidence. If neither action is projected,
neither action-specific proof is required, but installed-source, writer,
lease, cleanup scope and restore-runner attestation are still mandatory.
Even complete synthetic proofs produce REVIEW_REQUIRED with
execution_authorized=false, never a board execution permit.

The pure assess_passive_retention function validates timestamp order,
same-volume effective capacity, counts and at most 120-second intervals
for the counter correlation. It returns separately the net root/MP4/SRT
decrease and a possible prune signal. Such a signal never asserts
prune causality, exact moved-file counts, installed source or writer
lifecycle, four-channel throughput, or execution authorization.

## Long-running owner-runner remains a separate admission prerequisite

The host's command limit is 1,800 seconds; a 45-minute campaign needs
2,700 seconds plus an independently guaranteed 1,860-second cleanup/restore
budget. Chaining calls or starting an untracked background process does NOT
satisfy this. A future owner-controlled durable runner needs a persistent
recovery journal, verified source and lease identity, stage deadlines,
automatic abort and restoration when control is lost, writer EOS/FD
closure and rename/fsync guarantees, and a confirmed final clean state.
Until installed, tested and explicitly approved, the
full_campaign_restore_runner_verified field must remain unverified.

The canonical pinned no-delete safety gate is untouched and remains valid
for its static scenario. This proposal never contacts the board, deletes
recordings, modifies ORD settings, merges PR181/PR228, triggers OHT packets,
or starts the long campaign.
