# 장애 복구 판정과 ORD·VCM 검증 범위

## 복구 불확실 상태의 실행 중단

장애 해제 명령의 실행 결과만으로 복구 성공을 판정하지 않는다.
프로파일에 recovery_verify_command가 있으면 정확한 OK 응답으로 사후조건을 확인한다.
사후조건이 실패하면 FAULT_RECOVERY_UNVERIFIED로 기록하고 같은 타겟의 다음 시험을 중단한다.

### 단일 케이스 CLI

| 종료 코드 | 의미 |
|---|---|
| 0 | 검사와 정리 성공 |
| 1 | 일반 검사 실패 또는 실행 실패 |
| 4 | 정리/복구 상태를 확인하지 못함. 같은 타겟의 후속 시험 중단 필요 |

--all은 코드 4에서 남은 케이스를 실행하지 않는다. --watch도 반복을 중단한다.
일반 검사 실패(1)의 기존 연속 실행 정책은 유지한다.
검사 예외나 보고서·알림 저장 예외가 겹쳐도 복구 불확실 상태에서는 코드 4를 유지한다.
복구 상태가 확인된 일반 실행·보고 오류의 기존 예외 전달 동작은 유지한다.
Plan CLI의 기존 PASS/FAIL/WARN/설정 오류 코드 매핑은 별도이며 바뀌지 않는다.
Plan gate는 FAULT_RECOVERY_UNVERIFIED가 있으면 합격률 임계값보다 우선하여 FAIL을 낸다.

### 설정 파일 복원 확인

호스트 스냅샷 복원에 실패해 보드 백업으로 전환할 때는 백업을 먼저 읽어
JSON 원본을 확인한다. 호스트에 시험 전 스냅샷이 있으면 백업 바이트가
그 스냅샷과 같아야 한다. 부팅 중 갱신되어 시험 설정으로 바뀐 백업은
복사하기 전에 거부한다.

백업 복사와 동기화가 성공한 뒤 실제 대상 파일을 다시 읽어 바이트가
일치하는지 확인한다. 백업 누락·손상, 복사 실패, 읽기 실패, 내용 불일치는
복원 예외가 되며 후속 reset/reboot를 실행하지 않는다. CLI는 기존 복구
실패 경로를 통해 종료 코드 4를 반환하고 후속 배치 시험을 중단한다.

호스트 스냅샷이 없으면 검증 가능한 보드 백업으로의 복원만 확인할 수 있다.
이 경우 시험 시작 전 원본과 같은지는 별도 증거 없이 보증하지 않는다.

### Plan의 최종 캠페인 복원

최종 설정 복원이나 복원 후 재부팅에서 예외가 나면 `section: cleanup`,
`case_name: campaign_restore`인 별도 실패 결과를 추가한다.
실제 케이스의 성공/실패 결과는 그대로 보존한다. `campaign_restore`는 내부 결과용 이름이다.
이 결과의 `FAULT_RECOVERY_UNVERIFIED`는 gate 임계값보다 우선하므로
합격률 임계값이 0이어도 FAIL이고 Plan CLI 종료 코드는 기존 매핑에 따라 1이다.

JSON/JUnit/HTML/Markdown 보고서와 진행 이벤트에 이 실패를 포함한다.
정상 케이스 2개 뒤 최종 복원이 실패하면 뷰어는 완료 3/3, PASS 2, FAIL 1로 표시한다.
중도 중단된 원래 케이스는 pending으로 남는다.
정상 복원에는 추가 결과가 없으며, 진행 이벤트 저장 오류가 복원 실패를 숨기지 않는다.
KeyboardInterrupt가 진행 중이면 그 중단을 보존하고 복원 오류도 로그에 남긴다.

### 웹 실행 결과

복구 불확실 결과에는 status: ERROR와 recovery_unverified: true가 포함된다.
선택 실행과 태그 실행은 이 결과 뒤에서 중단한다. 응답의 count는 실제 실행 수,
requested는 요청한 수, stopped는 복구 불확실 상태로 중단했는지를 뜻한다.
자동 실행은 running을 false로 바꾸며 /api/status의 stop_reason에 원인을 남긴다.

검사 수집 예외와 복구 실패가 함께 발생해도 복구 중단 신호를 유지한다.
히스토리나 대시보드 저장이 실패하면 복구 불확실 결과에 저장 오류를 덧붙인다.
복구 불확실 결과가 없는 일반 보고서 저장 예외는 기존처럼 호출자에게 전달한다.

### 스트리밍 실행

스트리밍 실행은 설정 적용의 일반 예외와 검사 엔진 생성·순회 예외를
ERROR로 종료한다. 설정 적용·장애 주입을 시도하기 전에 거부된 실행은
복원이나 reset 없이 끝낸다. 적용이나 주입을 시도한 뒤의 실패는 정리한다.
일부 검사 결과가 이미 있으면 보존하고 실행 오류 결과를
추가한 뒤 정리를 수행한다. 개별 검사 수집 실패의 기존 FAIL 판정은 유지한다.
카메라 복구 요청의 결과가 불확실한 상태에서는 추가 정리나 reset 요청을
제출하지 않고 FAULT_RECOVERY_UNVERIFIED를 알린다.

프로파일 로딩, SSH 생성·접속, preflight, 설정 변경 전 read-back 예고와
SetupManager 생성에서 예외가 나면 SSE는 오류와 `done(ERROR)`를 한 번씩
내보내고 종료한다. 비정상 종료를 방치해 keep-alive ping이 반복되지 않게 한다.
설정 실패 후 정리 또는 최종 teardown 검증이 실패하면 `done`에도
`recovery_unverified: true`가 포함된다. 히스토리나 대시보드 저장 오류는
`done(ERROR)`로 끝내며 이미 관측된 복구 불확실 상태를 숨기지 않는다.
저장 실패가 발생했다면 보고서 파일이 완전하게 기록되었다고 추정하지 않는다.

### CLI·웹·병렬·스트리밍 공통 setup 실패 경계

`run_setup`은 실제 `apply_changes` 또는 fault 주입 명령 **호출 직전**부터
`_setup_mutation_attempted`를 참으로 기록한다. 이 표식은 명령의 성공 여부나
최종 복구 성공을 뜻하지 않는다. 네 실행 경로는 다음 우선순위를 지킨다.

1. 카메라 복구 결과 불명(`_recovery_outcome_uncertain`)이면 새 복원·reset을
   제출하지 않고 복구 미확인으로 종료한다.
2. 변경·주입 시도가 명시적으로 없었다면(`_setup_mutation_attempted is False`)
   미변경 타겟의 오래된 백업을 복원하거나 재부팅하지 않는다.
3. 변경·주입을 시도했거나 시도 여부가 불명확하면 setup 설정과 teardown 설정을
   함께 넘겨 정리를 한 번 시도한다. 복구 명령이 없어도 변경한 설정을 복원한다.
   정리 중 오류가 나면 복구 미확인으로 종료한다.

CLI는 복구 미확인에 종료 코드 4를 사용하고, 웹·스트리밍은
`recovery_unverified: true`를 표면화한다. 병렬 실행도 복구 미확인에
`status: ERROR`와 `recovery_unverified: true`를 기록한다.
설정 실패 후 정리를 완료했더라도 실패한 케이스를 PASS로 바꾸지 않는다.
병렬 실행의 예외 경로는 `finally`에서 정리를 다시 실행하지 않는다.

## 프로세스 재시작 스모크

process_restart_smoke는 setup에서 장애를 한 번 주입하고,
이후 custom_commands 수집은 읽기 전용으로 수행한다.
Plan의 케이스 재시도는 single_injection 케이스를 다시 주입하지 않는다.

공식 gstApp 복구 API는 이 시험의 앵커와 INJECT_VERIFIED 또는 AUTO_RECOVERY_FAILED
표식이 확인될 때만 호출한다. 이전 시험 표식, 주입 전 실패, 표식 누락은 자동 복구를 보류한다.
이 표식은 분산 잠금이나 타겟 측 fencing을 대신하지 않는다.

복구 검증은 다음 조건을 한 묶음으로 최대 6회 관찰한다.
관찰 사이 대기는 5초이며, PID가 먼저 나타나도 나머지 조건을 계속 기다린다.

- gstApp, ord, vcm 프로세스가 존재하고 zombie가 아님
- cam_state가 healthy
- 설정에 적힌 ORD·VCM TCP 포트가 리슨 중

모든 조건이 성공한 뒤에만 해당 시험의 marker를 정리한다.
상태가 불확실하거나 STALE_MARKER이면 marker를 보존하고 후속 시험을 중단한다.
이 검사는 로컬 모의 테스트만으로 실제 watchdog 복구 성능을 보증하지 않는다.

## 녹화·SRT 관측

720p_4ch의 SRT 개수는 현재 boot_id와 일치하는 시험 앵커 이후의 파일만 센다.
앵커가 없거나 이전 부팅의 것이면 FAIL:NO_SESSION_ANCHOR로 실패한다.
이는 파일 생성 여부 검사이며, .srt.part를 포함하므로 최종 저장 완료 증거와는 구분한다.

verify_vcm_operation의 최근 상태 파일 검사는 5분 이내 갱신된 일반 파일만 센다.
빈 디렉터리나 최근 생성한 하위 디렉터리는 상태 파일로 인정하지 않는다.

## ORD·VCM 통합 sanity와 실제 이벤트 시험

ord_vcm_integration은 프로세스, 설정 JSON, 설정된 TCP 포트, 공유 정책,
녹화 상태 경로를 읽기 전용으로 확인한다. 설정 오류와 TCP 연결 실패는 실패로 판정한다.

설정 JSON은 각 파일에 문서가 정확히 하나 있어야 한다. 빈 파일, 구문 오류,
여러 문서를 이어 붙인 파일은 포트 검사 전에 거부한다. 카메라 설정과
ORD/VCM 설정은 파일별로 읽어 출처를 보존하며 한 파일의 문서 두 개를
두 설정 파일로 오인하지 않는다.

제품 소스 `jhw7500/pim-package-jhw@f26b99fabda5955229041ecd182091fb8b340137`에서
OHT 바이너리 헤더, 버전 응답, ORD→VCM IPC, SRT 세션 이름, COPY 시간창을 확인했다.
계약과 근거는 [OHT 프로토콜과 수락 절차](oht-protocol-and-acceptance.md)에 기록했다.
실제 보드의 설치 바이너리가 이 소스와 일치하는지는 아직 확인하지 않았다.

`oht_protocol.py`는 버전 조회 요청의 직렬화와 완전한 응답 1개의 검증만 제공한다.
소켓 연결, 실제 요청 전송, COPY/PRI/RTC 명령은 구현하지 않는다.
응답의 원시 버전 5바이트는 전체 패키지 버전이나 Git SHA를 대신하지 않는다.

현재 sanity의 TCP 연결과 소스 JSON 검사는 활성 merged runtime 일치,
OHT 이벤트 처리, 최종 영상/SRT 저장 완료를 보증하지 않는다.
실제 이벤트 E2E는 입력·VCM 처리·선택된 녹화 구간·최종 산출물의 증거를 함께 요구한다.

## 로컬 검증

프로젝트 루트에서 pytest와 ruff check를 실행한다.
관련 회귀 테스트는 tests/test_integration_recovery_batch_stop.py,
tests/test_integration_recovery_outcome_propagation.py,
tests/test_cases_process_recovery_readiness.py,
tests/test_cases_recording_window_anchor.py,
tests/test_cases_vcm_recording_state.py,
tests/test_cases_ord_vcm_integration.py,
tests/test_plan_campaign_restore_outcome.py, tests/test_oht_protocol.py에 있다.

실보드 시험은 현재 호스트의 검증된 보드 잠금 경로와 해당 시험 범위의 권한을 확인한 뒤 수행한다.
WSL·빌드서버의 검증 결과와 적용 소스는 각각 기록한다.
