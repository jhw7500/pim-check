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

## 3. 검증

- 단위: `uv run pytest` 733 passed (baseline 708 + 신규 25) — fixture 는 보드 실측 출력.
- 보드: 아래 "보드 검증" 절 참조 (검증 실행 결과는 커밋 로그에 기록).
