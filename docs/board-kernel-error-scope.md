# PIM Board 커널 오류: 출처·시간범위·증거 계약

## 실제 보드 관측 (2026-10-30 KST)

- 타겟: i.MX8MP, `192.168.214.4`; **공식 PIM 독점 lease** 안에서만 읽기 전용 확인.
- `/proc/sys/kernel/random/boot_id`: `5c1762b0-374c-4b2d-adfb-b8b5419b0d80`.
- `uptime -s`: `2026-10-26 08:53:00 UTC`; `/tmp/pim_check_anchor`는 없었다.
- `/var/log/cantops/kern.log`에서 기존 `board_error_detect`가 집계한 4줄:
  - 2026-10-26 08:53:27.381, 08:53:28.245 UTC: I²C adapter 2, ch0, MCP4018(0x2f) wiper 0x10 write fail after 5/5 attempts, ret=-6.
  - 2026-10-26 08:53:27.633, 08:53:29.245 UTC: 같은 ch0 single applied fail (ret=-6).
  - 즉 4개 **로그 줄**은 각각 2개의 쓰기 실패 및 후속 적용 실패 기록으로, 독립적인 4회 고장이 확정된 것은 아니다.
- `kern.log` 마지막 수정/로그는 부팅 직후 2026-10-26 08:53:30 UTC 부근. `rsyslog`는 조사 시점 active. 이후 커널 오류가 없었다고 단정할 증거는 아니다.
- 부팅이 다른 이전 로그에서도 ch0/ch1/ch2의 MCP4018 write fail ret=-6이 반복됐다. 일회성 잡음으로 제외하지 않는다.
- 2026-10-30 추가 관측: MAX9296 adapter 2/2-0048 `prepare` state CONSUMED, errno=0, worker_errno=0. `health_raw`: enabled ch0 link/serializer/ISP OK, streaming true. gstApp health의 ch0 gstreamer/recording OK. **MCP4018 wiper 실제 적용/응답 성공 증거는 아님**(sensor deep probe UNKNOWN). 재부팅/설정변경/파괴적 시험 미실시.

## 기존 QA의 결함

기존 `board_error_detect`는 체크 이름이 "세션 이후"였지만 세션 앵커가 없으면 `uptime -s`로 폴백해 과거 부팅 최초 30초 오류까지 재집계했다. 4일 후 QA에서 과거 오류가 "최근 발생"처럼 보일 수 있다. 또한 `grep`으로 읽을 로그 파일이 없으면 파이프라인 `wc -l`이 0을 반환해 **진단 자료 누락을 거짓 PASS**할 수 있었다.

## 수정 계약 (WSL 소스, read-only)

`profiles/cases/board_error_detect.yaml`의 원본 대상 및 제외 필터는 보존하며 판단을 유지한다:

- 현재 boot_id에 맞는 세션 앵커만 `SCOPE=SESSION`; 없거나 다른 boot_id이면 `SCOPE=BOOT_FALLBACK`, 시작은 `uptime -s`.
- 결과가 정상 0줄일 때만 정확히 `0`을 반환. 1줄 이상이면 `COUNT=N SCOPE=... START=... BOOT_ID=... FIRST=... LAST=...`과 제한된 RECORD 샘플을 반환해 **기존 expected: "0"**으로 FAIL 처리한다. FIRST/LAST는 각각 최대 180바이트, RECORD는 최대 32줄·줄당 512바이트이다. 전체 커널 로그는 출력하지 않는다.
- 로그 원본이 없거나 비어 있으면 `NO_SOURCE`, 시작 시간이 고정 `YYYY-MM-DD HH:MM:SS` 형식과 맞지 않으면 `INVALID_START_TIME`: 모두 FAIL, 조용한 PASS 불가.
- BSP 노이즈 예외 목록은 기존 그대로. 진짜 MCP4018 오류를 새로 제외하지 않는다.
- 무오류 `0`은 지정 구간에서 일치하는 *로그 줄이 없음*만 의미한다. 독립적인 MAX9296/MCP4018 I²C 건강도나 소스의 지속적 수집 보증은 아니다.

## 검증·후속

- `tests/test_cases_board_error_scope.py`: 실제 YAML command를 POSIX shell로 실행해 4줄 분류/범위/원본 누락/시간 오류/출력 상한/expected0 fail-closed를 검증.
- `tests/test_cases_kernel_log_source.py`: 새 `awk END` 형태의 exit 0+출력 규약을 기존 커널 로그 소스/시간범위/BSP 보호와 함께 검사.
- 변경 소스 Git 커밋, CI 상태, 실보드 재검증은 해당 실행 시점의 증거로 별도 기록해야 한다.
- ch0 MCP4018 -ENXIO의 전원·물리 링크·장치 준비 시퀀스와 저항/펌웨어 재시도 간격은 현재 **확실하지 않음**. 드라이버 분석과 실제 Wiper readback/안전한 통제 환경에서의 재현 검증이 후속이다.

## 드라이버 소스 조사: MCP4018이 왜 실패할 수 있는가

읽기 전용으로 확인한 별도 `max9296` 저장소의 `max9296.c` (관측 SHA256 `c0c31169...`)는 다음 동작을 구현한다.

1. `max9296_apply_channel_controls()`의 `mcp_active`는 `ch_ctrl->led_flash & (1 << 8)`로 결정된다. 실제 로그에도 `mcp:on wiper:0x10`이 출력됐다.
2. `max9295_mfp4_set(..., true)`로 MAX9295 MFP4를 제어해 MCP4018 VCC를 켠 다음, `mcp4018_write_wiper()`가 serializer I²C translator를 거치는 host address `0x2f`에 **1바이트 write**를 시도한다.
3. `i2c_transfer()`가 실패하면 최대 5번 반복(간격 1ms)하고 최종 음수 코드(`-6` 관측)를 로그에 남긴다. 즉 실제 `0x2f` 접근 실패이지 kernel 로그 문자열 분류 오탐이 아니다.
4. 에러가 `first_err`에 누적되어 `single applied fail`이 기록되지만, 함수 최종 반환은 `exposure_err`다. 따라서 MCP4018 실패만 있었다면 호출 측은 오류 반환 없이 진행할 수 있으며, `prepare errno=0`/gstApp health OK만으로 Wiper 쓰기 성공을 증명할 수 없다.
5. 비슷한 write fail은 이전 다른 부팅·채널에서도 보여 전원 준비 지연·MAX9295 MFP4 전원 제어·serializer translator 매핑·MCP4018 부품 실장/배선/I²C NACK 중 **무엇이 실제 원인인지는 확정되지 않았다**. 소스와 현재 설치 모듈의 정확한 빌드 일치도 별도 검증 사항이다.

실보드에는 I²C write/readback, 플래시 점등, 전원 토글, 카메라 재시작·부팅 반복을 수행하지 않았다.


## 구조화된 증거 및 연관 후보 묶음 (2026-10-30 후속)

`evidence_format: kernel_log_v1`인 커스텀 검사만 증거를 파싱한다. 원래 출력과
`expected: "0"` 비교가 최종 판정이며, 파서는 실패를 PASS/WARN으로 바꾸지 않는다.
다른 커스텀 검사 및 전체 체크 개수는 바뀌지 않는다.

- `BOOT_ID`는 수집 시작·종료에 일치하는 현재 부팅 ID다. 각 과거 로그 줄의 부팅을
  독립적으로 증명하는 필드가 아니므로 `per_line_boot_identity_verified=false`를 기록한다.
- `RECORD`는 디코딩된 원문 접두 샘플이다. 바이트 동일성을 검증한 원본 아카이브가 아니다.
  `SAMPLES`, `OMITTED`, `CLIPPED`로 누락·줄 잘림을 구분하며, 둘 중 하나라도 있으면
  `status=partial`, `grouping_complete=false`다. 오류 총 줄 수는 샘플 개수로 줄이지 않는다.
- 같은 I²C adapter와 채널이며 첫 줄부터 2초 이내인 샘플만 후보로 묶는다. 서로 다른
  장치, 장치 식별 불가, 시간 역전은 별도 묶음이다. 연속 2초 간격의 무한 연결은 하지 않는다.
  후보 묶음 수는 독립 장애 건수나 확정 원인이 아니다. `grouping_is_causal=false`와
  `root_cause=UNKNOWN`을 유지한다. 정확히 같은 샘플 접두의 반복 수는 별도 기록한다.
- 소스가 오류 줄만 수집하므로 복구 상태는 `UNVERIFIED`다. 프로세스 healthy, 이후 오류
  부재, 그룹화만으로 복구를 단정하지 않는다. 깨끗한 `0`도 복구는 `NOT_ASSESSED`다.
- stdout가 0이더라도 awk 읽기 실패가 있으면 `READ_ERROR`; 수집 도중 부팅 ID가 바뀌면
  `BOOT_CHANGED`; 없는/잘못된 ID는 `INVALID_BOOT_ID`다. 달력상 불가능한 시작 시각과
  오류 줄의 잘못된 시각 형식도 FAIL로 남긴다. 임시 보드 파일은 만들지 않는다.
- 기존 초 단위 `substr($0,1,19) > bt` 범위와 BSP 제외 목록은 그대로다. 시계 변경이나
  세션 앵커 의미 자체를 재설계한 것이 아니므로 무오류 결과를 완전한 로그 수집 보증으로
  확대하지 않는다. 새 date/awk 조합의 실제 타깃 검증은 별도다.

텍스트 보고서는 COUNT/SCOPE/BOOT_ID/후보 묶음/샘플 범위/복구 미확인을 표시하고
커널 제어문자는 이스케이프한다. JSON의 `checks[].evidence[].kernel_log`에는
허용된 진단 필드와 원문 샘플만 보존한다. 일반 custom command 문자열, 설정, 자격증명을
통째로 복사하지 않는다. 기존 evidence 없는 체크의 JSON 형식은 보존한다.

검증은 `tests/test_checks_kernel_evidence.py`의 모의 SSH·원문 리플레이와 실제 로컬 셸
검사로 수행한다. 이번 후속에서는 실제 SSH·보드 lease·재부팅·하드웨어 조작을 수행하지
않으며, 앞선 실보드 수집 차단을 다른 도구나 명령으로 우회하지 않는다.

SSH `None`의 기존 `(got: None)` 표식을 유지하여 인프라 실패를 지속 하드웨어 장애로
고정하지 않는다. 읽기 직전 로테이션/비움으로 입력이 0줄이 된 경우에도 `NO_SOURCE`로
실패하며, 잘못된 문자 인코딩은 예외로 전체 QA를 중단하지 않고 invalid evidence로 남긴다.
