# pim-check 실전 검증 중 발견된 이슈

## 타겟 환경 이슈

### 1. SD 카드 슬롯 없음 (환경 의존)
- **발견 시점**: fault_sd_readonly 케이스 첫 실행
- **내용**: 타겟(iMX8MP)에 외부 SD 슬롯이 없고 eMMC(mmcblk2)만 존재. `/mnt/sd` 경로 자체가 없음
- **조치**: SD 케이스를 eMMC 루트 파티션 + `/root/shared_v` 기반으로 변경
- **교훈**: 케이스 작성 시 타겟 하드웨어 구성을 먼저 확인해야 함

### 2. SD 파일시스템 타입 가정 오류
- **발견 시점**: fault_sd_readonly 케이스
- **내용**: `vfat` 가정했으나 실제는 `ext4`
- **조치**: 파일시스템 타입 체크 대신 마운트 여부 체크로 변경

### 3. BG_Check_for_pim 미실행
- **발견 시점**: 모든 케이스
- **내용**: 타겟에서 BG_Check_for_pim 프로세스가 실행되지 않음
- **영향**: required 프로세스로 설정되어 모든 헬스체크에서 FAIL
- **상태**: 타겟 측 이슈 (pim-check는 정상 감지)

### 4. 지속적 고온 (89-93C)
- **발견 시점**: 모든 케이스
- **내용**: HD(1280x720) 모드에서도 SoC 온도 89-93C 유지
- **영향**: thermal check 항상 FAIL (max 85C 기준)
- **상태**: 하드웨어/쿨링 이슈

## 셸 명령 이슈

### 5. grep -c exit code 문제
- **발견 시점**: fault_cam_disconnect, fault_high_cpu 타겟 실행
- **내용**: `grep -c`는 매치 0건일 때 exit code 1 반환. `ssh.run()`이 non-zero exit를 None으로 처리하여 `|| echo 0`이 추가 "0" 출력 → "0\n0" 반환
- **조치**: `|| echo 0` 대신 `|| true` 사용하여 grep의 "0" 출력은 유지하면서 exit code만 0으로 변경

### 6. ls glob 패턴 빈 결과 처리
- **발견 시점**: fault_gstapp_crash 케이스
- **내용**: `ls /tmp/core.gstApp* 2>/dev/null`이 파일 없으면 빈 문자열 반환 (None 아님) → `expected: "NONE"` 불일치
- **조치**: `test -n "$(ls ...)" && echo FOUND || echo NONE` 패턴으로 명시적 결과 반환

### 7. cam_state 디렉토리 구조 불일치
- **발견 시점**: 최초 타겟 연결 시
- **내용**: Plan에서 `ch0_state`, `ch0_err_streak` 가정했으나 실제는 `state`, `streak` (루트) + `channels/ch0_error` (서브디렉토리)
- **조치**: cam_state.py를 실제 구조에 맞게 재작성

## 설계 교훈

1. **셸 명령은 반드시 타겟에서 먼저 수동 실행하여 출력 확인** — exit code, 줄바꿈, 빈 출력 등 예상과 다를 수 있음
2. **custom_commands의 expected 비교는 strip 후 정확 일치** — 공백/줄바꿈 주의
3. **하드웨어 의존 체크(SD, 온도)는 타겟별 프로파일 필요** — 범용 케이스와 타겟 특화 케이스 분리 권장
