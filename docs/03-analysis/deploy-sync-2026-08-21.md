# 배포 조합 sync — pim-package-jhw · max9296 · gstApp (2026-08-21)

2026-06-15 재검증 캠페인 이후 세 저장소의 변경분(각 179/60/92 커밋)을 조사해
pim-check 검사 영향 항목을 식별·반영한 기록. 보드 실측은 유선 192.168.214.4
(2026-08-21, max9296 2.5 + gstApp cacb78a 배포 상태)에서 수행.

## 1. 보드 배포 상태 (실측 ground truth)

| 항목 | 실측값 |
|------|--------|
| max9296.ko | version **2.5** (srcversion 41D8E9E128B7BB8873D14D7), 로드됨 |
| prepare ABI | `/sys/bus/i2c/devices/{1,2}-0048/prepare` — `state=IDLE ... errno=0 worker_errno=0` |
| health_raw ABI | `/sys/bus/i2c/devices/{1,2}-0048/health_raw` — JSON schema 1, adapter 1→ch2/ch3, 2→ch0/ch1 |
| gstApp | `/usr/local/bin/gstApp` (camera-health v1 producer 내장, jhw7500/gstApp@cacb78a) |
| health producer | `/run/pim-camera/gstApp.json` 1Hz 발행 (schema 1, atomic rename) |
| systemd 유닛(신규) | `cam-operate.service`(=chk_cam_operate.sh, enabled) · `sd-mount.service` · `pim-config-guard.service` · `pim-camera-config.service` · `pim-gate.service` |
| 신규 config | `/opt/pim/config/camera_capture_map_v1.json` · `camera_health_error_codes_v1.json` |
| 기존 경로 유효 | `/root/shared_v/edgeconf_pim.json`(실디렉토리), `/tmp/cam_state/{state,streak,channels/}`, "Session complete" 로그 |
| dmesg fsync | `[I2C:1][max9296.c:4619] max9296_fsync side fps : 15, ...` — **포맷 변경** |

## 2. 식별된 영향 항목과 처리

### 반영 완료 (이번 변경)

| # | 변경 출처 | 영향 | 처리 |
|---|----------|------|------|
| 1 | max9296 2.5 — fsync 로그에 mode 단어 삽입 (`fps :` → `side\|single\|dual fps :`) | **파손**: setup.py 카메라 readiness 게이트가 구 리터럴 grep 으로 0 매칭 → 카메라 케이스 전멸 | `FSYNC_MARKER_RE` ERE 로 교체(구/신 포맷 모두 매칭), `grep -c` → `grep -cE` (setup.py) |
| 2 | max9296 2.5 — prepare/health_raw sysfs ABI 신설 | 신규 단언 가능 | `checks/max9296_abi.py` 신설: modinfo version=2.5 + prepare 파싱(errno/worker_errno=0, ≠FAILED) + health_raw(schema 1, deserializer OK, enable 채널 link up, serializer OK) |
| 3 | gstApp cacb78a — camera-health v1 producer 내장 | 신규 단언 가능 | `checks/cam_health.py` 신설: `/run/pim-camera/gstApp.json` 신선도(uptime 대비 stale_ms, boot_id 대조) + FAIL observation 0 |
| 4 | gstApp — max9296 prepare 통합 실패 시 LOG_CRIT (`[MAX9296_PREPARE] invalid request` / `owner lock failed` / `LEGACY_NO_ABI`) | 신규 오류 신호 | base.yaml `logs.error_patterns` 에 `MAX9296_PREPARE` 추가 (정상 prepare 는 NOTICE 라 -p err 에 안 잡힘) |
| 5 | pim-package-jhw — chk_cam_operate.sh 가 `cam-operate.service` 유닛으로 관리 | fault_gstapp_crash 케이스의 `journalctl -u chk_cam_operate.service` 가 잘못된 유닛명 (2026-06 백로그 #2 의 전제 변경) | 유닛명을 `cam-operate.service` 로 정정 + killcam 로그 패턴 추가 + teardown 정정 |
| 6 | (3의 파생) gstApp 을 의도적으로 죽이는 fault 케이스에서 producer stale 은 주입 효과 | false-fail 방지 | fault_gstapp_crash 에 `cam_health: {path: null}` override |

새 체크 2종은 `checks/__init__.py` ALL_CHECKS 등록(8→10) + base.yaml 기본값 +
retry_policy 로 전 플랜에 기본 적용된다. 케이스별 비활성화는
`expected_version: null` / `path: null`.

### 영향 없음 확인 (조사로 배제)

| 항목 | 근거 |
|------|------|
| "Session complete" 녹화 로그 | gstApp muxSinkBin.cpp:121 잔존 — recording 체크 유효 |
| RTSP sync / v4l2-sync-trace / stall 주입 CLI 옵션 (gstApp 신설) | chk_cam_operate.sh 가 전달하지 않음(기본 OFF) → edgeconf 스키마 변경 불요 |
| `/root/shared_v/` 경로 | 실디렉토리로 유지 — setup.py 경로 유효 |
| cam_state state/streak | 보드 실측 존재(healthy/0) + `timestamp`·`recording/` 파일 추가는 비파괴 |
| 프로세스명 (gstApp/BG_Check_for_pim/chk_cam_operate) | 배포 스크립트 동일 — process 체크 유효 |
| 단일채널 serializer 0x40 고정, 전원 refcount, pinctrl (max9296) | 드라이버 내부 동작 — 검사가 단언하던 항목 아님 |
| `.VHL_CAM.app` 키 (chk_cam_operate.sh 가 "gstApp" 보정) | schema axes 는 케이스 생성용 축만 정의 — 정적 키는 스키마 대상 아님 |

### 보류 (아직 단언하지 않음 — enable 시 후속 반영)

| 항목 | 사유 |
|------|------|
| `/run/pim-camera/max9296.json` · `pim-probe.json` producer, aggregator(`camera_healthd.py`), `/tmp/cam_final_health` 상태 | Phase 0 — 유닛([Install] 없음) 보드 미설치. ISI 통계 게이트 방식·cable round 원인 확정 후 enable 예정 (pim-package-jhw 18f545b) |
| binary-manifest sha 대조 | 매니페스트가 pim-package-jhw 미merge 브랜치(chore/gstapp-max9296-prepare-binaries)에만 존재 + sha 는 빌드마다 변동 — version/ABI 수준 단언으로 대체 |
| prepare 쓰기(병렬 prepare 능동 테스트) | 검사 도구는 read-only 원칙 — 드라이버 게이트(G1~G4, 2.5 에서 closed)가 담당 |

### 별도 백로그와의 관계

2026-06 캠페인 백로그(fault_sd_unmounted, gen under-config, dry-run cosmetic,
custom_commands stale-read 등)는 보드-무관 검사 버그로 이번 sync 범위 밖.
단 fault_gstapp_crash 는 유닛 신설로 전제가 바뀌어 이번에 정정했다 (위 #5).

## 3. 리뷰 반영 (같은 날, 독립 리뷰 패스)

리뷰가 경계 조건 결함 5건(major)을 재현해 반영함:

- **예외 유출 차단**: 두 신규 체크에서 JSON top-level/observations/channels 가
  기대 모양이 아니면 AttributeError 대신 FAIL reason (엔진은 SSH 예외만 잡음 —
  체크 예외는 케이스 전체 결과를 날린다).
- **음수 age 상한**: mid-read 미래 skew 허용을 1s 로 제한 — 그 이상 미래는
  boot_id 로 못 거른 이전 부팅 잔존으로 FAIL (죽은 producer PASS 차단).
- **uptime 불능 표면화**: 기준 시계를 못 읽으면 "freshness not verified" FAIL
  (신선도 검사의 조용한 증발 금지).
- **kill 주입 케이스 opt-out 완결**: gstApp 을 pkill 하는 케이스는 전 195개 중
  정확히 2개(fault_gstapp_crash, process_restart_smoke) — 둘 다 cam_health +
  max9296_abi 비활성. fault_gstapp_zombie 는 의도적으로 켜 둠(zombie 시
  producer 정지 → stale FAIL 이 참양성).
- **부팅 직후 grace**: 파일 부재 + uptime < early_boot_grace_sec(180) 이면
  NEED_PRODUCER_SNAPSHOT_AFTER_BOOT stabilization 신호로 분류
  (verify_retry 단일 출처에 토큰 추가) — sticky merge 로 인한 재부팅 직후
  flaky hard-fail 방지. 그 외 노출은 process 체크(gstApp required)가 먼저
  실패하는 구간과 동일해 신규 위험 아님.
- **fsync mode open set**: `( [a-z-]+)?` — 드라이버가 mode 단어를 추가해도
  재파손되지 않음. **로그 패턴 앵커**: `\[MAX9296_PREPARE\]`.
- **0ch 방어**: deserializer/link 단언은 enable 채널이 있을 때만 — 전채널 off
  구성에서 드라이버의 저전력 거동은 미실측이라 구조 단언만 남김.
  (후속 보드 확인 항목: 0ch edgeconf 적용 상태의 health_raw deserializer 상태)

의도적으로 받지 않은 지적: base.yaml 의 expected_version 고정(배포 조합 단언이
이 체크의 목적 — 다른 보드는 target/케이스 레이어에서 null override),
config 경로 shell quoting(저장소 전반 컨벤션과 통일 유지), 엔진 스냅샷
테스트의 체크 수 hard-pin(구성 회귀를 잡는 의도된 고정).

## 4. 타겟 검증 (smoke 플랜, 2026-08-21)

`pim_check.py --plan smoke --host 192.168.214.4` 엔드투엔드 1차 실행이
**2차 파손 위치를 적발**했다:

- **케이스 YAML 의 fsync fps 추출 grep 21건** (profiles/cases/*.yaml 의
  custom_commands `dmesg max9296_fsync fps (expected N)`): setup.py 와 동일한
  구 리터럴 `'max9296_fsync fps : [0-9]+'` 를 사용 → 2.5 보드에서 빈 문자열
  추출 → 카메라 케이스 전부 FAIL. `( [a-z-]+)?` 삽입으로 구/신 포맷 모두
  추출하도록 21개 파일 일괄 교체 (generated/·schema·generator 는 해당 없음 확인).
- 1차 실행 결과: 1/8 PASS — process_restart_smoke(opt-out 정정 케이스) 통과,
  카메라 5케이스는 위 grep 단독(3건) 또는 grep+비트레이트(2건) FAIL,
  board_hw_check 는 **신규 cam_health 가 실제 4채널
  GSTREAMER_SOURCE_STALL 을 포착**해 FAIL(참양성), config_integrity 는 스톨을
  감지한 chk_cam_operate 워치독의 보드 재부팅과 겹쳐 NO_SSH.
- readiness `[ready] camera_init` 이 매 케이스 통과 — setup.py 의 fsync ERE
  수정이 실플로우에서 검증됨.

### 2차 실행 (케이스 grep 수정 후) — 최종 verdict: 5/8 PASS

| 케이스 | run 1 | run 2 | 판정 |
|--------|-------|-------|------|
| 720p_2ch | FAIL(구 grep) | **PASS** | 수정 유효 |
| 720p_4ch | FAIL | FAIL: ch2 bitrate 2904/4096 | **FW 회귀 후보 (2/2 재현)** |
| fhd_2ch_03 | FAIL(구 grep) | **PASS** | 수정 유효 |
| fhd_3ch_012 | FAIL(구 grep) | **PASS** | 수정 유효 |
| fhd_4ch | FAIL | FAIL: ch0 65kbps/8192 | **FW 회귀 후보 (2/2 재현, 무기록 수준)** |
| process_restart_smoke | PASS | **PASS** | opt-out 정정 유효 (2/2) |
| board_hw_check | FAIL(STALL) | FAIL: WiFi(wlp1s0) IP 미할당 | 유선 랩 env 아티팩트 (2026-06 기지, 백로그) |
| config_integrity | NO_SSH | **PASS** | run 1 은 워치독 재부팅 겹침 |

### 회귀 후보 근본 원인 조사 (2026-08-21, 같은 세션에서 완결)

두 FAIL 은 서로 다른 두 메커니즘으로 판명 — 둘 다 4ch 부하 회귀가 아니었다.

**A. fhd_4ch ch0 65~85kbps (콜드부트 2/2 재현) = ch0 과노출 백색 프레임**

- 직접 원인 (프레임 레벨 실측): 콜드부트 gstApp 인스턴스의 ch0 영상이 전면
  백색 포화 (signalstats YAVG **254.8**/255, 육안 확인 — 구조물 윤곽만 희미).
  h265 가 플랫 프레임을 스킵 수준으로 인코딩 (I-frame 4.4KB, P ~400B, GOP 정상)
  → 60초 세그먼트 ~500KB ≈ 65kbps. 인코더/파이프라인/전송 결함 아님.
- 결정적 대조: process_restart_smoke 의 pkill 재기동 인스턴스는 **같은 부팅,
  같은 설정 로그** (`ch0 ae_on:0, ae_gain:8192, exp_time:33000, bps:8192,8192`)
  인데 정상 노출 (YAVG 196.6) → 이후 세션 7.5~7.9Mbps 완벽 추종.
- 조건: ch0 은 이 케이스에서 유일한 수동노출 극단값 채널 (ae off + gain 8192
  + exp 33ms — 레지스터 검증용 설정). 콜드부트 vs 웜 재기동에서 이 수동
  노출의 실효 상태가 다르다. prepare 로그 차이: 콜드 `action=4 elapsed_ms=11552
  state 0→2` (FW 로드) vs 웜 `action=2 elapsed_ms=0 state 4→4` (adopt).
- 6월 동일 케이스(같은 gain 8192) bitrate PASS → 8월 FW 스택 변화로 도입
  (후보: gstApp 3.0 의 dual-slot 채널 컨트롤 라우팅 신설(videoBin "csi0 CH0
  slot"), driver 2.5 prepare 경로, ISP init 순서). **어느 쪽이 충실 적용인지
  미확정** — gain 8192@33ms 가 원래 백색이어야 맞다면 결함은 오히려 "웜
  재기동에서 수동 설정 미적용"이다. gain 스케일 정의와 적용 경로를
  gstApp/max9296 측에서 확인 필요.
- 재현 절차: fhd_4ch 콜드부트 → 첫 세션들 ch0 mp4 크기 확인 (~500KB/분이면
  재현). 파일 증거: VD3001_20260821_094100~094400-ch0.mp4 (run 2),
  090800~091100 (run 1).

#### A-후속: gain 적용 경로 규명 (같은 날, 센서 레지스터 실측)

**판정: 백색 = ae_gain 8192 의 충실 적용이 맞다.** 백색 상태에서 AR0234
센서 실측 — ch0 아날로그 게인 0x3060=**0x0040(≈16×, coarse 2^4 —
datasheet 표준 해석 기준)** vs ch1(auto)=0x0000(1×), 적분시간(0x3012)은
버스 공유로 동일. ISP 미러도 AE_CTRL 0x0290(manual)+AE_GAIN 0x2000(8192)
+EXP 33000us 로 커맨드값 그대로. 즉 manual gain 8192 가 실효되면 물리적으로
백색 포화가 정상이다.

**진짜 결함은 "적용 여부가 라이프사이클에 따라 비결정적"인 것:**

| 시점 | 상태 | 근거 |
|------|------|------|
| 콜드부트 (3/3: run1·run2·실험) | **적용 → 백색** | 세션 ~500KB/분, 센서 16× 실측 |
| pkill→watchdog respawn 직후 | **미적용 → 정상** | 09:46~09:59 세션 7.5~7.9Mbps (센서 readback 은 미확보 — 잔여 확인 항목) |
| respawn 후 ~30분 내 (10:16) | **자발 재적용 → 백색 재진입** | 10:16~10:17 세션 ~500KB, 센서 16× 실측. 트리거 미상 (재부팅 전 boot 의 영속 저널 역추적 과제) |

**경로 (전 구간 코드 확인):** gstApp `V4L2_CID_GAIN_CH0`(videoBin, 2026-02
부터 불변) → 드라이버 s_ctrl → `ctrl_cache.ch0.gain` →
`max9296_apply_cached_controls()`(**`pending` 플래그 게이트**) →
`max9296_apply_channel_controls()`(6월판과 diff 0: MANUAL 시드 → EXP →
모드 → **AE_GAIN 상시 기록**) → AP1302 0x5006 → 센서 16×. gstApp·드라이버
apply 로직 모두 6월과 동일하므로, June-差는 **apply 가 불리는 라이프사이클
타이밍**(8월 prepare/stream 분리 재편) + v4l2 프레임워크의 동일값 dedup
(재기동 시 s_ctrl 미호출 → pending 미설정 → flush 생략) 조합으로 추정.
6월의 PASS 는 측정 창에서 gain 이 실효되지 않던 우연일 가능성이 높다.

**후속 조치 배분:**
- gstApp/max9296 측: 적용/미적용 비결정성(재기동 시 재적용 누락 + 주기
  재적용 트리거) 일관화 — v4l2 dedup 하에서 재기동 후 cache 재flush 경로
  설계 필요. 10:16 자발 재적용 트리거 식별 (영속 저널).
- pim-check 측: fhd_4ch/720p_4ch 등 4ch 케이스의 `ae_gain: 8192`(+33ms)는
  실효 시 백색이 되는 설정 — 레지스터 검증 목적과 bitrate 검증이 한
  케이스에서 충돌. gain 축은 별도 케이스로 분리하거나 실효-호환 값으로
  조정 필요 (백로그).

**보드 사용 기록 (다른 태스크 참조용):** 이 조사 과정에서 10:18Z 재부팅
1회, 10:24Z 경 `pkill -9 gstApp` 1회(워치독 자동 respawn) 수행. 이후
10:32Z 경 잔여 확인용 `pkill -9 gstApp` 1회 추가 (아래 A-확정).

#### A-확정: enable 스레드 하드코딩 AE vs 캐시 apply 의 소모성 pending race

잔여 확인(respawn 직후 센서 실측 + dmesg 타임라인)으로 기제가 완전히
특정됐다.

**respawn 직후 실측 (10:32Z)**: 센서 0x3060 = 16×→**1× 리셋**, ISP
AE_CTRL = **0x0299(AUTO)** (gstApp 은 manual 을 명령), AE_GAIN 미러만
0x2000 잔존, ch0 .part 20여 초 만에 33.7MB(정상 8Mbps).

**기제 (max9296.c, dmesg 실측으로 확인):**
- enable 스레드(~4721)는 스트림 개시 후 **하드코딩 AE 초기화**를 쓴다:
  `0x5002=0x0290` → 100ms → **`0x0299(AUTO)`** → AWB(0x5100=0x115f) →
  LSC(0x54a0=0x3fff). 직후 주석 그대로 "Override hardcoded AE/AWB init
  with V4L2 cached controls" — `max9296_apply_cached_controls()` 호출.
- 그러나 override 는 `if (!ctrl_cache.pending) return;`(2695) 가드 뒤에
  있고 **pending 은 소모성**이다.
- **콜드부트 (dmesg t=26~28s)**: 하드코딩 댄스가 먼저, apply 가 나중
  (`[0x11] 0x290→0x290`) → 설정값(manual+gain 8192) 최종 승리 → 백색.
- **respawn (t=460~464s)**: s_stream(1) 경로가 apply 를 먼저 실행해
  pending 소모(manual 기록) → 3초 뒤 enable 스레드 하드코딩 댄스가
  **AUTO 로 덮어씀** → override 는 pending=false 로 **no-op** → AUTO
  최종 승리 → 정상 화상 (설정 소실).
- 결론: **최종 AE 상태 = 하드코딩 AUTO vs 캐시 apply 의 "마지막 승자"**
  이며, 소모성 pending 게이트 때문에 라이프사이클 순서에 따라 승자가
  뒤집히는 race. ~30분 후 자발 재적용(10:16)도 같은 기제(모종의
  이벤트가 pending 재설정 → apply 재실행)로 설명된다.

**FW 측 수정 방향 (max9296):** enable 스레드의 override 를 pending 과
무관하게 캐시에서 무조건 재적용하거나(권장), 하드코딩 AE 댄스를 설정
인지형으로 바꾸거나, override 직전 pending 을 재설정. 어느 쪽이든
"하드코딩 초기화 + 조건부 덮어쓰기" 구조 자체가 취약하다는 점을 함께
검토할 것. → **이슈로 handoff 완료: jhw7500/max9296#26**
(https://github.com/jhw7500/max9296/issues/26)

**잔여 (선택):** 10:16 자발 재적용의 정확한 트리거 이벤트 — 구 boot 의
journald kernel 수집 부재로 로그 확보 실패. 라이브 재관측(~30분 보드
점유 + dmesg watch)으로만 특정 가능. 기제가 규명됐으므로 FW 수정
검증 시 함께 관측하면 충분.

**B. 720p_4ch ch2 2904~3205kbps@4096 (2/2, 세션간 안정) = h265 전환 미보정**

- 직접 원인: ch2 화상은 정상 (YAVG 126.3, 풀레인지 0~255) — 결함 아닌
  **h265 VBR 이 정적 실험실 장면에서 목표 4096 을 못 채우는 언더슛**
  (세션간 2816~2936 안정 수렴 = 장면 한계).
- 결정적 대조군: 같은 케이스 ch3(목표 8192)은 edgeconf `profile: 9`(invalid)
  → fallback 으로 **qp_min/qp_max 0,0 (무클램프)** 이 되어 8192 추종 성공.
  클램프(qp 22~42) 있는 ch2 만 목표 미달.
- 유래: `.VHL_CAM.enc: "h265"` 와 채널별 gop/profile/quant/qp_min/qp_max 가
  2026-08-06 gstApp #27(H.264/H.265 스위칭·튜닝, 기본값 h264) 이후 보드
  edgeconf 에 수동 설정됨 — **pim-check 스키마·케이스가 모르는 키라 케이스
  전환 시 리셋되지 않는 config 드리프트**. 6월엔 스위칭 기능 자체가 없어
  h264 고정이었고 bitrate 기대치도 h264 기준.
- 성격: FW 회귀가 아니라 코덱 전환에 따른 검사 기대치 미보정 + 케이스의
  인코더 키 미통제. 조치 후보(백로그): ① 케이스가 enc/qp 를 명시 설정
  (h264 고정 또는 h265 기대치 재보정) ② schema.yaml 에 enc 축 추가
  ③ bitrate 체크가 enc 값을 읽어 기대치 스케일 조정.

**기타**

- run 1 의 1회성 전채널 GSTREAMER_SOURCE_STALL (신규 cam_health 가 포착,
  워치독 재부팅으로 자기회복, run 2 미재현): A 와 같은 콜드부트 창에서
  발생 — 연관 가능성만 기록.
- board_hw_check WiFi 체크: 유선 랩 환경 상시 FAIL (2026-06 확정 env
  아티팩트) — wired-mode 허용 여부는 별도 백로그.

## 5. 백로그 착수 — 케이스 스위프 (gain 재설계 + h265 기대치 보정)

근본 원인 규명(§4)에 따라 케이스 21파일 일괄 정비 (같은 날):

- **manual gain 8192 → 512** (모든 설정 케이스): 8192 는 실효 시 센서 ≈16×
  → 백색 포화로 bitrate 검증과 물리적으로 충돌 (max9296#26 race 가 "적용"
  으로 떨어지는 콜드부트마다 재현). 512 는 레지스터 기록 경로 검증력이
  동일하면서 비포화 (ch3 이 512 로 8192kbps 추종한 실측 근거). 극단값
  자체에 고유 단언이 없음을 확인함 (검증은 mirror readback — 값 무관).
- **AE_GAIN(0x5006) 기대값 갱신** (multi_* 11건: '0x200x00'→'0x020x00') +
  **smoke 3케이스에 AE_GAIN readback 신설** (기존엔 smoke 에 gain 레지스터
  검증이 없었음 — manual 채널 ch0/ch3 에 추가).
- **인코더 상태 명시 고정** (bitrate 체크 보유 19파일): `enc: "h265"`
  (배포 의도 코덱) + enabled 채널별 `qp_min/qp_max/profile: [0, 0]`
  (명시적 무클램프 — invalid profile 9 fallback 우연 의존 제거). 근거:
  h265 + qp 클램프(22~42)는 정적 장면에서 목표 미달(720p_4ch ch2 ~2.9M@4096
  2/2), 무클램프 ch3 은 8192 추종. 케이스가 인코더 키를 통제하지 않으면
  보드 edgeconf 드리프트(8/6 튜닝 세션 잔존)가 검증을 비결정화한다.
- 잔여 리스크 기록: FW #26 미수정 상태에서는 warm-경로(무재부팅 killcam)
  케이스의 AE_CTRL=manual 단언이 흔들릴 수 있음 — 현행 플랜은 reboot 경로
  중심이라 안정. FW 수정 후 재검토.

## 6. 검증 요약

- 단위: `uv run pytest` 전건 통과 (baseline 708 → 748) — fixture 는 보드 실측
  출력, 리뷰가 재현한 실패 모드 전부 회귀 테스트로 고정.
- 보드: 192.168.214.4 라이브 스냅샷 10/10 PASS + smoke 타겟 검증 (1차: 파손
  적발, 케이스 grep 수정 후 재실행 결과는 커밋 로그에 기록).
