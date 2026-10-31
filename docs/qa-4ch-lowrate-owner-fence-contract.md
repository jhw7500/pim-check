# PIM no-MCP experimental low-rate 4ch — review and owner-fencing contract

**Engineering candidate only. No physical 4ch acceptance, no installation, and no permission to mutate the board.**

## Exact scope

- Case: `profiles/cases/720p_4ch_mcp_absent_lowrate_120s.yaml`, cloned from `720p_4ch_mcp_absent.yaml` with only title/description/tags, four `bps` pairs `512/512`, and a case-level `monitor.duration_sec: 120` override. Base case `720p_4ch`, original MCP-absent case, all four LEDs OFF, QP/AWB/AE, 720p30, SRT, strict recording and hardware read-back checks are unchanged.
- Tag `smoke` deliberately removed; manual owner-reviewed candidate, not a normal smoke test. Exact target `192.168.214.4`, MCP4018 hardware absent. The 512kbps HEVC output and four-channel pipeline performance are **NOT** established hardware capabilities.
- Setup stabilization stays 200s, monitor120s, conservative restore allowance300s =620s potential writer exposure, 4×512=2048kbps, 155000KiB conservative video, 88 estimated root entries. These include two entries/channel/60s segment as a deliberately high-side MP4+SRT bound.
- Under the last verified **historical** 2026-10-31T09:23Z board snapshot root886/1000, used207467396KiB and 90% ORD limit, existing PR189 pure gate returns `CAPACITY_REVIEW_ONLY`, **never execution_authorized true**. This snapshot expires after120s; it cannot permit a later real run. The original 15360kbps case and the 800sec lowrate case still block on the same snapshot.
- Strict custom checks search the **second finalized** MP4 session since boot and check all 4 FPS and bitrate plus ch0 MP4/SRT. A 120-second monitor may be insufficient if camera startup/session boundaries delay second finalize. Do not weaken/skip those checks; if failure is only due to missing second finalize, consider a separately reviewed longer monitor after a NEW capacity calculation.

## Product owner stop/fencing findings

- Product source `pim-package-jhw` commit `f26b99fabda5955229041ecd182091fb8b340137`, `docs/camera-health/cam-recovery-operations.md`: `cam-recoveryctl request gstapp_stop` terminates gstApp, but `cam_liveness_tick` can immediately submit `gstapp_restart` in the same owner iteration. **This is not a persistent fence**.
- Installed board read-only systemd fields (2026-10-31 09:58Z): cam-operate.service `ActiveState=active, SubState=running, Restart=on-failure, KillMode=control-group, MainPID=465, TimeoutStopUSec=2min 10s`, owner lifecycle `ACTIVE`, no pending/active request. Installed `cam_operate_stop.sh` SHA256 `cf3b4948147339b344836944366d1814f88349268047e5f787d7e9a12077a9f0`, `cam-recoveryctl` SHA256 `60057ba404f2f67ba99948a2d09f5fbaf567f671e7c573504f5f13ffa92f08d8`.
- The owner stop utility `cam_operate_stop.sh` is associated with a systemd stop path and STOPPING lifecycle, but an externally invoked stop alone does not yield a durable, session-bound restart inhibition token. It also does not by itself satisfy the existing recovery adapter's `owner_idle` and post-restore healthy-consumer contract.
- The new PR191 candidate read-only Inspector intentionally reports `writer_fence_session: null`. It cannot infer a lasting Writer stop from a snapshot of zero writable FDs. No existing product API has been verified to produce a persistent writer fence under the exact jhw-control lease. DO NOT spoof the field or mark `writer_closed=true` without real multi-process FD proof.

## Owner engineering requirements before approved hardware run

1. An owner-controlled maintenance/quiesce protocol that **prevents** liveness restarts or external startup during the full operation, remains effective across its session, and exposes an unforgeable lease/session-bound proof. A manual `gstapp_stop` or `systemctl stop` string is not such proof by itself.
2. Persist immutable LED-OFF edgeconf+ORD/VCM+runtime baselines with exact file and binary hashes before any configuration change. The desired historical source SHA256 is `1e26aa8262257b0e4b75efae73e2da735d53d2ce3e5974c49419818838f087f7` and all LED flags false; re-read live before mutation.
3. Distinguish `writer closed` (all writable file FDs absent, EOS/finalizer status) from `writer cannot reopen` (owner-maintained fence). The second must not be inferred from the first. Never delete existing SD media to fake headroom.
4. Full official `jhw-control board with` exclusive long lease with remaining time through restore; one-use host-owner fixed-command approval, vetted action source SHA, and durable systemd restorer that outlives chat process and supervises 4ch/45m campaigns.
5. After owner-acknowledged restore, verify exact source+runtime hashes, owner/gstApp/ORD/VCM readiness, LED OFF, journal cleanup and actual completed MP4/SRT session files. Failing / uncertain postconditions retain `RESTORE_PENDING` or `FINAL_ATTESTATION_PENDING` and block subsequent test.
6. Only a reviewed source/installed binary path may invoke mutation. The new source transfer to buildserver for PR191 Inspector was previously safety-denied and remains unavailable for this task; do not repurpose generic host commands as a bypass.

## Result classification

YAML parser and PR189 admission contract tests = offline acceptance of **profile structure**. They do NOT establish new hardware bitrate support, video quality, physical 4-channel frame flow, independent writer EOS/fsync, owner-fence correctness, real systemd persistence, or physical restoration.
