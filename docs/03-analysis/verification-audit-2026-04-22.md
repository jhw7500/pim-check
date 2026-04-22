# 검증 프로그램 독립 감사 리포트 (2026-04-22)

**배경**: 2026-04-20 발견된 per-channel 단독 모드 검증 결함(`ch3-isp-register-issue.md` 참조)의 범위가 예상보다 넓을 수 있다는 판단에 따라, 독립 에이전트(general-purpose, opus 모델)에게 전체 검증 프로그램을 감사 요청. READ ONLY 감사.

**감사 기준 (결함 3종)**
- **A — State isolation 누락**: 테스트가 대상 키만 세팅하고 다른 state는 베이스에 의존 → 의도와 다른 모드로 실행되어 버그 은폐
- **B — OR-fallback verification**: `cmd_A || cmd_B` 패턴으로 실제 동작 모드 불문하고 값만 맞으면 PASS
- **C — 동작 모드 검증 부재**: 테스트가 주장하는 조건이 실제로 성립했는지 독립 확인하는 단계 없음

---

## 심각도 집계
| 심각도 | 개수 | 주요 영역 |
|--------|------|----------|
| CRITICAL | 3 | runner fallback, recording_time/muxer, capture |
| HIGH | 13 | fps, bitrate, channel_combo, hflip 커버리지, runner cleanroom |
| MEDIUM | 10 | 수동 케이스(verify_*), cam_state, recording 체크 |
| LOW | 4 | 단위 테스트 약한 assertion, AWB measure 타이밍 |
| **합계** | **30** | |

---

## CRITICAL

### FINDING #1 — recording_time/muxer 축 state isolation + weak assertion
- **위치**: `profiles/schema.yaml:95-143`
- **카테고리**: A + C
- **증상**:
  - `.VHL_CAM.recording_time`/`.VHL_CAM.muxer` 한 키만 수정. 채널 enable, fps, capture, bps 미지정
  - 검증은 `find ... | xargs stat --printf='%s' ... || echo 0` + `expected_min: 1000`
  - 녹화 파일 **duration** 미검증 → 5분 설정인데 1분짜리 파일이어도 PASS
  - `|| echo 0`으로 모든 실패(SD 미마운트, find 오류 등)를 0으로 은폐
- **권장**: 채널 enable 1ch 고정 + `ffprobe -show_entries format=duration` 검증 + 허용 오차

### FINDING #2 — capture 축에 verify가 아예 없음
- **위치**: `profiles/schema.yaml:84-93`
- **카테고리**: A
- **증상**: 설정만 토글, verify 필드 부재. cam_state healthy만 보고 PASS. capture 기능이 dead code여도 감지 불가
- **권장**: edgeconf 반영 + 캡처 파일 생성 확인 추가

### FINDING #12 — comprehensive runner에 0x3c fallback 재현
- **위치**: `run_comprehensive_verify.py:172-177`
- **카테고리**: B
- **증상**: dual 주소로 읽다가 실패 시 0x3c로 재시도하여 기대값이면 PASS. schema에서 OR-fallback 제거한 것과 **동일 효과**가 runner 수준에서 재현
- **영향**: `comprehensive_results.json`(2026-04-21 96/96 PASS) 전체 신뢰도 훼손. 특히 dual mode 진입 실패가 single으로 우회돼도 통과된 기록 가능

---

## HIGH

### FINDING #3 — fps 축 verify 부재
- **위치**: `profiles/schema.yaml:42-51`
- **카테고리**: A + C
- edgeconf 값조차 확인 안 함. 실제 스트림 fps 무검증. fps 축은 사실상 no-op
- **권장**: jq edgeconf + ffprobe `stream=r_frame_rate`

### FINDING #4 — muxer 검증이 반대 확장자 혼재 허용
- **위치**: `profiles/schema.yaml:131-143`
- **카테고리**: B + C
- `find ... *.mp4 -mmin -2 | wc -l >= 1` 만 확인 → mp4로 전환했는데 일부 채널 여전히 ts로 저장돼도 PASS
- **권장**: 테스트 시작 시각 이후 파일만 세고, **반대 확장자 0건** 강제

### FINDING #5 — bitrate ch0 전용 + ffprobe 미실측
- **위치**: `profiles/schema.yaml:148-187`
- **카테고리**: A + C
- ch0만 수정. edgeconf JSON 값만 확인(gstApp이 무시해도 PASS). ch1-3 누락
- **권장**: bitrate_ch0~3 per-channel + ffprobe 실측 비트레이트 검증

### FINDING #6 — channel_combo 모드 진입 무검증
- **위치**: `profiles/schema.yaml:646-732`
- **카테고리**: A + C
- 4채널 enable만 명시, 다른 설정(vflip/hflip/ae/awb/bps/fps/muxer 등) 잔존
- `channel_count=N` 검증은 journalctl 로그 문자열에만 의존
- **실제 i2c single/dual 모드 진입 여부 무검증**
- `1ch_ch1` combo 누락
- **권장**: default reset + i2cdetect로 기대 주소 응답 패턴 검증 + 1ch_ch1 추가

### FINDING #7 — hflip ch0 전용 (ch1-3 커버리지 누락)
- **위치**: `profiles/schema.yaml:53-82`
- **카테고리**: 기타 (누락)
- vflip/ae/awb는 per-channel 정정됐으나 hflip은 ch0 only
- **권장**: hflip_ch0~3 per-channel로 확장 (대칭 확보)

### FINDING #8 — hflip_off 기대값이 하드웨어 초기값과 동일
- **위치**: `profiles/schema.yaml:69`
- **카테고리**: C (변형)
- expected=`0x000x00`인데 reset 기본값도 같음 → 쓰기 미반영을 감지 불가
- **권장**: pre/post 비교 또는 hflip_on 전용 검증 의존

### FINDING #9 — recording_time duration 미검증 + 오류 은폐
- FINDING #1과 동일 (세부 카테고리 분리)

### FINDING #10 — generator 구조적 state isolation 결함 + smart_verify 비대칭
- **위치**: `generator.py:74-155`, `run_smart_verify.py:200-208`
- **카테고리**: A
- generator가 combination.values만 복사 → 다른 축 state 잔존
- smart_verify의 `global_effective`가 "B + 720p일 때만 global override"로 하드코딩 → fhd B는 fps/muxer 회귀 감지 안 됨
- **권장**: generator에 known_defaults 자동 세팅 + res별 비대칭 제거

### FINDING #11 — smart_verify 0x3c fallback
- **위치**: `run_smart_verify.py:279-286, 499-507`
- **카테고리**: B
- `actual != expected and addr != 0x3c`이면 0x3c 재시도 후 기대값이면 PASS
- **권장**: fallback 시 result=PASS_VIA_FALLBACK 별도 분류 또는 passed=False

### FINDING #12 — comprehensive runner fallback (위 CRITICAL 참조)

### FINDING #13 — comprehensive reset 범위 부족
- **위치**: `run_comprehensive_verify.py:192-258`
- **카테고리**: A
- `build_reset_changes`가 SETTING_TESTS(vflip/hflip/ae/awb)만 리셋. bps/ae_gain/exp_time/fps/recording_time/muxer/capture 미리셋
- **권장**: 모든 축 default 리셋 또는 baseline 덮어쓰기

### FINDING #14 — channel_verify/failed_retry runner는 cleanroom 없음
- **위치**: `run_channel_verify.py`, `run_failed_retry.py`
- **카테고리**: A
- 케이스 간 edgeconf baseline 리셋 단계 없음. 비-vflip/ae/awb 축(bps/fps/muxer 등)이 누설
- **권장**: 각 케이스 시작 전 `edgeconf_pim_base.json` 복사 또는 jq 전량 defaults 세팅

### FINDING #15 — mixed_combo runner가 일부 축만 reset
- **위치**: `run_mixed_combo_verify.py:104-134`
- **카테고리**: A
- enable/vflip/hflip/ae/awb만 세팅. bps/ae_gain/exp_time/fps/muxer/recording_time 미설정
- 비활성 채널의 vflip/hflip/ae/awb defaults 복원 안 함
- 실제 i2c 모드 진입 검증 없음 (기대값만 맞으면 PASS)
- **권장**: 전역 reset + i2cdetect 모드 확인

### FINDING #29 — infer_agent 조합키 매칭 silent skip
- **위치**: `infer_agent.py:78-79`
- **카테고리**: B
- 조합키 매칭 실패 시 조용히 검증 스킵 → 커버리지 과대표시
- **권장**: None이면 WARN 기록

---

## MEDIUM

### FINDING #16 — verify_cam_flip 수동 케이스 ISP 미검증
- **위치**: `profiles/cases/verify_cam_flip.yaml`
- edgeconf jq만 확인, ISP 레지스터 없음

### FINDING #17 — verify_channel_2ch/4ch 모드 미검증
- 상태 파일 의존, i2c 모드 확인 없음

### FINDING #18 — verify_resolution_hd/fhd 실제 해상도 미검증
- 위치: `profiles/cases/verify_resolution_*.yaml`
- CPU만 봄. ffprobe width/height 필요

### FINDING #19 — verify_vcm_operation recording 체크 약함
- `find ... -mmin -5 | wc -l >= 1`: 디렉토리 자체도 카운트. `-type f` 누락. srt policy 충돌

### FINDING #20 — verify_ord_operation `expected_min: 0`
- 0건도 통과 → 의미 없음. 1 이상으로 상향 필요

### FINDING #21 — RecordingCheck: journalctl 문자열 의존
- `checks/recording.py:14-26`
- 로깅 포맷 변경 취약 (false FAIL/PASS 양방향)

### FINDING #22 — cam_state: per-channel error 미검증
- `checks/cam_state.py:33-66`
- global state만 보고 channels dict 실제로 validate 안 함

### FINDING #23 — config_edgeconf_camera vs runner 조합 시 충돌
- ch0/ch1 enable=true 강제 기대가 1ch_ch3 테스트와 충돌

### FINDING #27 — 1ch_ch1 combo 누락
- FINDING #6의 하위

### FINDING #28 — setup.py apply_changes 개별 라운드트립 + swallow
- `setup.py:26-45, 80-83`
- 중간 crash 시 edgeconf 불완전. reboot 실패 swallow

---

## LOW

### FINDING #24 — test_setup 약한 assertion
- `tests/test_setup.py`
- jq 표현식 정확성 미검증 → 특수문자 주입 가능성 놓침

### FINDING #25 — 60fps 시나리오
- `run_smart_verify.py:167-175`
- 하드웨어 지원 확인 없이 60fps 하드코딩

### FINDING #26 — AWB measure 타이밍
- `run_smart_verify.py:62-67`
- 복귀 타이밍이 30s stabilize보다 길면 플래키

### FINDING #30 — verify_ord_operation bash 비교
- `[ "$E1" = "$E2" ]` 양쪽 빈 값이면 match → 실패 은폐

---

## 신뢰도 재평가

| 영역 | 신뢰도 | 근거 |
|------|--------|------|
| `mixed_combo_results.json` (2026-04-22) | **85%** | fallback 없음, 가장 엄격 |
| vflip/ae/awb per-channel schema (오늘 정정) | 75% | 결함 A/B 회피. #7, #8 잔존 |
| cam_state/recording log 기반 체크 | 50% | 로깅 포맷 취약 |
| runner 계층 (`run_*.py`) | **40%** | #11-#15 fallback/누설 |
| recording_time/muxer/bps/fps | **20%** | #1-#5, #9 다수 |
| `comprehensive_results.json` (2026-04-21) | **WARN 재분류** | #12 fallback으로 PASS 기록 신뢰 불가 |
| `channel_verify_results.json` / `channel_retry_results.json` | **WARN 재분류** | #14 cleanroom 없음 + schema fallback 당시 적용 |

---

## 최우선 수정 3가지

1. **FINDING #11, #12** — runner들의 0x3c fallback 제거. 기대 모드에서 기대 주소만 읽고, 값 불일치 시 즉시 FAIL.
2. **FINDING #1, #9** — recording_time/muxer verify를 ffprobe duration + 배타 확장자 검증으로 재설계.
3. **FINDING #10, #13, #14, #15** — 모든 runner/generator에 per-test cleanroom reset 도입 (edgeconf baseline 복사 또는 jq 전량 defaults).

---

## 결론

- 2026-04-22 이전의 모든 대규모 검증 결과(96/96 PASS 등)는 **신뢰 불가**. runner/schema의 다중 fallback + state isolation 부재로 false PASS가 구조적으로 가능했음.
- 오늘 schema 정정은 중요한 첫 걸음이지만 **runner 계층 정정 없이는 효과 제한적**.
- 이 감사 리포트에 따라 CRITICAL 3 + HIGH 13 정정 후 전체 재검증 필요.

---

## 변경 이력

- 2026-04-22: 독립 감사 에이전트가 1회 실행하여 30건 발견. 본 문서로 정리.
