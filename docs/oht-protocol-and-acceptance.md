# OHT 프로토콜과 QA 복구 수락 절차

## 확인 범위와 소스

분석 기준은 제품 저장소 `jhw7500/pim-package-jhw`의
`f26b99fabda5955229041ecd182091fb8b340137`이다. 아래 계약은 이 소스에 한정한다.
실제 보드의 패키지·바이너리·활성 설정과 일치하는지는 별도 확인해야 한다.
이 문서 작성 시점에는 실보드 요청 전송, 장애 주입, 재부팅을 수행하지 않았다.

- [ORD packed 구조체와 명령 상수](https://github.com/jhw7500/pim-package-jhw/blob/f26b99fabda5955229041ecd182091fb8b340137/ord/tcpServer.h)
- [ORD 수신·버전·COPY 처리](https://github.com/jhw7500/pim-package-jhw/blob/f26b99fabda5955229041ecd182091fb8b340137/ord/tcpServer.cpp)
- [VCM IPC 처리](https://github.com/jhw7500/pim-package-jhw/blob/f26b99fabda5955229041ecd182091fb8b340137/vcm/ipc.cpp)
- [VCM runtime 설정·SRT 기록](https://github.com/jhw7500/pim-package-jhw/blob/f26b99fabda5955229041ecd182091fb8b340137/vcm/tcpServer.cpp)
- [녹화 finalizer](https://github.com/jhw7500/pim-package-jhw/blob/f26b99fabda5955229041ecd182091fb8b340137/dist/pim/opt/pim/bin/chk_cam_operate.sh)

## 버전 조회의 바이너리 계약

멀티바이트 정수는 network byte order(big-endian)이며 공통 헤더는 10바이트다.
기계 ID는 정확히 6바이트다. ORD는 설정된 `VHL_CAM.vhl_name`에서 앞 6바이트를 쓰고
짧으면 0으로 채운다. 이 Python helper는 ID를 묵시적으로 자르거나 채우지 않는다.

| 필드 | 요청 offset / 크기 | 값 |
|---|---|---|
| machineType | 0 / 2 | 12851, 12853, 12855 중 하나; 기본 12851 |
| machineID | 2 / 6 | 대상 기계 ID 원시 6바이트 |
| cmd | 8 / 2 | 41004 (`0xa02c`) |
| eventType | 10 / 1 | 3 (`EVT_TEST`) |

요청은 총 11바이트이며 `struct` 형식은 `!H6sHB`다.
버전 응답은 총 16바이트이며 `machineType=6`, 동일한 기계 ID,
`cmd=41005`, `eventType=3`, 원시 버전 5바이트 순서다.

```python
from oht_protocol import build_version_request, parse_version_response

request = build_version_request(b"ABC123")
assert request.hex() == "3233414243313233a02c03"
# 소스 계약으로 직접 작성한 합성 예시이며 실제 보드 capture가 아니다.
reply = bytes.fromhex("0006414243313233a02d03312e322e33")
assert parse_version_response(reply, b"ABC123") == b"1.2.3"
```

`oht_protocol.py`는 순수 직렬화/검증 모듈이다. 네트워크 연결이나 보드 작업은 없다.
응답 길이, byte order에 따른 필드 값, machineType, machineID, cmd, eventType를
모두 검증하며 앞뒤 데이터가 붙거나 JSON인 응답을 받아들이지 않는다.
버전은 decoding·strip 없이 원시 5바이트를 반환하므로 NUL과 비ASCII 값도 보존한다.

ORD의 EVT_TEST 구현은 `dpkg -s pim-mp` 출력에서 5바이트만 복사한다
(`ord/tcpServer.cpp:1945–1970`). 이 값으로 전체 버전, build identity, Git SHA를
입증할 수 없다. 정상 형식의 응답도 패키지 조회 성공까지 보증하지 않는다.
테스트 fixture는 소스에서 도출한 합성 패킷이며 실제 장치와의 호환 검증은 남아 있다.

이 프로토콜에는 길이 필드, delimiter, checksum, request sequence ID가 없다.
현재 ORD 서버는 개별 `recv()` chunk를 직접 파싱하며 TCP frame 누적기가 없다
(`ord/tcpServer.cpp:1696–1704,1754–1777`). 향후 transport 구현에는 응답 deadline과
정확한 길이의 수신, 연결 단위 요청 직렬화, 실제 보드 capture가 필요하다.
현재 parser는 이미 완성된 단일 응답을 받는 API이며 TCP stream parser가 아니다.

## ORD→VCM→파일 완료의 증거

ORD→VCM 상태 전달은 System V 메시지 큐를 이용한다. VCM의 TCP endpoint는
별도의 overlay 구독·제어 경로다. 상태 패킷은 최대 55바이트이고, VCM이 처리한
시험용 node/offset/speed를 `SET_OVERLAY` 응답과 SRT 본문에서 대조할 수 있다
(`vcm/ipc.cpp:264–329`). 단순 ORD ACK나 TCP 연결 성공은 이 처리를 입증하지 않는다.

현재 제품 소스는 `/run/pim-camera/config/pim_runtime.json`의 merged 설정을 읽는다.
기존 sanity가 확인하는 `/root/shared_v/edgeconf_pim.json`과 `ord_vcm_conf.json`은
활성 runtime과 일치하는지 별도로 비교해야 한다. 실제 설치 버전 확인 전에는
기존 sanity를 전체 runtime 검증으로 확대 해석하거나 소스 경로를 임의 교체하지 않는다.

| 단계 | 필요한 증거 | 단독으로는 부족한 관측 |
|---|---|---|
| 요청 수락 | 원시 요청·응답과 정확한 command/type/ID | 포트 연결, 임의의 REP 문자열 |
| VCM 처리 | 시험용 상태 값이 overlay와 해당 시간의 SRT 본문에 반영됨 | 이벤트 ACK, GET_CONFIG |
| 녹화 구간 완료 | 활성 채널의 최종 non-`.part` 영상과 SRT, 시간 범위·내용 | `.srt.part`, `.srt_done`, `.all_done` 존재/소멸 |
| 이벤트 복사 완료 | ORD가 선택한 세션 목록과 실제 event 산출물의 채널·내용 일치 | COPY ACK만 수신 |

SRT 이름은 `${vhl_name}_YYYYMMDD_HHMM00-data.srt.part`이며 세션 키는
`YYYYMMDD_HHMM`이다. 최초 영상 구간의 초는 00이 아닐 수 있으므로 영상과
SRT의 전체 basename 일치를 요구하지 않는다. 분 단위 세션과 실제 timestamp·내용을
함께 비교한다. SRT는 주기적인 최신 상태 snapshot이며 요청별 파일이나 패킷별 cue가 아니다.

finalizer는 현재 분의 세션을 미루고, 대응 파일이 없어도 실패 수가 0이면
`.all_done` marker를 삭제할 수 있다. 따라서 최종 경로의 실제 파일을 확인해야 한다
(`chk_cam_operate.sh:949–1028`). SD/RAM 모드와 활성 최종 경로도 함께 기록한다.

### COPY 시간창

`call_copy`는 요청 timestamp 대신 보드 시각과 선택한 녹화 세션 시각을 사용한다.
`d=현재 시각−세션 시작`, `L=recording_time×60`, `M=margin_sec`, `D=evt_copy_delay`일 때
소스의 분기와 대기시간은 다음과 같다. 유효한 `0 <= M < L/2` 설정을 전제로 한 표다.

| 분기 | 시간 범위 | 대기시간 |
|---|---|---|
| HEAD | `0 <= d < M` | `L - d + D` |
| BODY | `M <= d < L - M` | `L - d + D` |
| TAIL | `L - M <= d < L` | `2*L - d + D` |

동일한 `(copyTime, copyType)` 요청은 새 worker 없이 반환할 수 있으므로
timeout 후 자동 재전송을 성공 증거 수집 방식으로 사용하지 않는다.
채널 수, SRT/vibration 활성화, target_copy, 경로 정책이 실제 선택 파일 수에 영향을 준다
(`ord/tcpServer.cpp:2073–2220`). 이번 변경은 COPY·PRI·RTC sender를 추가하지 않는다.

## 집중 수락 Plan

`profiles/plans/qa_recovery_acceptance.yaml`은 다음 순서를 고정한다.

1. `720p_4ch`: 설정 백업→720p 4채널/SRT 적용→reboot→녹화 검사.
2. `process_restart_smoke`: gstApp/ORD/VCM 장애를 한 번 주입하고 자동 복구와
   독립적인 복구 사후조건을 확인.
3. `verify_ord_vcm_integration`: 복구 뒤 프로세스·소스 설정·TCP·상태 경로 sanity.
4. Plan 종료 처리: 캠페인 시작 전 설정으로 복원하고 필요한 reboot 수행.

첫 실패 시 중단, case_retry=0, known issue 허용 안 함, 합격률 100%다.
`case_overrides.known_issues: []`로 상속된 WARN 예외를 해제한다.
기존 실행기는 known issue로 분류된 실패를 케이스 PASS로 취급하므로 gate 설정만으로는
다음 장애 주입 전 중단이 보장되지 않는다. 온도 실패 시 첫 케이스에서 멈추는 동작을
`tests/test_plan_recovery_acceptance.py`에서 실제 Plan/profile/gate 경로로 검증한다.
finalize 검사를 생략하거나 monitor cap을 추가하지 않는다.
최종 복원이 실패하면 `cleanup/campaign_restore` 결과가 추가되어 gate는 FAIL이다.
정상 완료에는 cleanup 결과가 추가되지 않는다.

`verify_vcm_operation`의 `srt_enable=false` 요구는 별도 baseline 전제다.
이 Plan은 첫 케이스에서 SRT를 켜므로 해당 케이스를 공통 preflight로 넣지 않는다.
필요한 경우 실제 기준 설정을 확인한 별도 시험으로 실행한다.

### 실행 전 확인

- 적용할 pim-check 소스의 commit과 dirty 파일 hash, 실행 호스트·경로를 기록한다.
- 보드 주소를 `192.168.214.4`로 명시하고, 전체 `pim-mp` 버전과 가능한 build/source
  identity, boot ID, 보드 시각/시간대를 기록한다. 버전 응답 5바이트로 대체하지 않는다.
- 원본 edgeconf/ord_vcm 설정과 활성 merged runtime, 채널·포트·SRT·저장 경로,
  설정 hash를 기록한다. 현 바이너리의 설정 계약이 다르면 시험 전에 해소한다.
- 이전 run의 복구 불확실 상태나 stale fault marker가 없는지 확인한다.
  남아 있으면 새 주입 전에 원인을 확인하며 marker를 임의 삭제해 진행하지 않는다.
- 검증된 독점 lease wrapper와 종료 시 복원 시간을 확보한다.
  WSL bridge는 최신 인계 기준 DRY_RUN 범위이므로 이 Plan의 실보드 실행 경로로 쓰지 않는다.

WSL의 보드 없는 로컬 검증:

```bash
uv run --offline --no-sync python -B -c 'from plan import load_plan, resolve_cases; p=load_plan("profiles/plans/qa_recovery_acceptance.yaml"); print(resolve_cases(p, "profiles"))'
```

다음 명령은 검증된 빌드서버에 동일 소스를 Git으로 전달하고 위 조건을 확인한 뒤
사용할 실행안이다. 이 문서에서 실행 완료를 주장하지 않는다.

```bash
cd /home/jhw/ai/opencode/projects/pim-check
scripts/with_pim_board.sh --for 90m --long-lease true \
  --purpose "QA recovery acceptance: recording, one restart, final restore" -- \
  uv run --offline --no-sync python -B pim_check.py \
  --plan qa_recovery_acceptance --host 192.168.214.4
```

90분은 예약 예산이다. 현재 wrapper의 30분 종료 유예와 1분 release 여유를 제외하면
일반 실행 시간은 약 59분이며, 관측된 실행 시간은 아니다. 실행 전 빌드서버의 wrapper
버전과 예산을 재확인한다. BUSY exit 4는 실패로 남기고 wait/retry/direct acquire를 하지 않는다.

### 합격과 중단

세 케이스의 PASS와 최종 복원 성공을 함께 요구한다. 종료 후 원본 설정 hash, 활성 runtime,
프로세스·readiness·포트, marker 정리를 대조하고 실행 시간대의 로그와 보고서를 보관한다.
timeout, 복구 불확실, 최종 복원 실패, 설정 hash 불일치가 있으면 후속 시험을 중단한다.
수동 복구 필요 여부를 owner/status/history와 함께 확인하고 자동 재주입하지 않는다.

산출물에는 실행 호스트·소스 identity, 실제 board/build identity, lease 시간,
설정 전후 hash, 케이스별 결과, 복원 결과, 원시 로그, JSON/JUnit/HTML/Markdown을 포함한다.
현재 Plan의 PASS는 위 복구 수락 범위이며 외부 OHT→최종 event 파일 E2E PASS를 뜻하지 않는다.


## OHT COPY E2E 실행 전 Fail-closed 게이트

`oht_e2e_gate.py`는 보드에 접속하거나 OHT 패킷을 보내지 않는
**오프라인 사전판정 함수**다. 실제 시험을 허가하거나 보드 exclusive lease를
대체하지 않는다. 사용자는 안전한 읽기 전용 증거를 확보한 뒤
`CopyPreflight`를 구성하고 `assess_copy_preflight`의 blocker가 없는지 확인한다.
입력이 빠지거나 bool을 숫자로 잘못 전달하면 거부한다.

다음 조건은 COPY 이벤트를 전송하기 전 각각 독립적으로 증명해야 한다.

- 분석한 ORD·VCM 프로토콜 소스와 **실제 설치 바이너리**의 동일성 또는
  검증된 대응 관계. 패키지 버전 문자열이나 EVT_TEST 5바이트 응답만으로
  소스-바이너리 일치를 추정하지 않는다.
- 보드 공식 `scripts/with_pim_board.sh`의 **실제 유효한 exclusive lease**,
  원본·merged runtime 설정의 포트·녹화·이벤트 정책 일치, 복구 저널 정상.
  메타데이터의 `exclusive_lease_verified=true` 표시는 실제 잠금을 획득하지 않는다.
- 기계 ID 정확히 **6바이트**, ORD·VCM TCP 설정 포트 유효,
  유효한 recording_time, SRT와 COPY 활성화. `jq length`의 문자열 글자 수는
  UTF-8 바이트 수와 같다는 보장이 없으므로 기계 ID 바이트 길이를 대신하지 않는다.
- 기존 녹화물 자동 삭제는 기본적으로 허용하지 않는다. ORD의
  `disk_manage=true`는 일반 녹화물 개수·용량 초과 시 정리를 시도하므로,
  `event_auto_remove=false`만으로 녹화물 삭제가 꺼진 것은 아니다.
  **두 정책의 활성/비활성 상태를 각각 확인**하며, 하나라도 미확인이면
  보수적으로 차단한다. 사용자가
  **이번 시험에서 기존 녹화물 삭제를 허용**하면
  `allow_existing_recording_deletion=True`를 명시할 수 있다.
  그러나 `automatic_cleanup_scope_verified=True`와
  `disk_management_enabled=True`를 **별도 검증된 근거로** 확인해야 하며,
  다른 설정·영상 이외의 파일 삭제를 포함하는 무제한 허가는 아니다.
  동의만으로 `event_auto_remove=true`가 정상적으로 동작한다거나
  ORD의 정리 범위가 적절하다고 추정하지 않는다.
- 삭제를 허용하지 않는 일반 모드에서는 보드 `df` 사용률에
  `minimum_headroom_percent`를 더한 값이 ORD `disk_limit_per`보다
  **작아야** 한다. 반면 기존 녹화물 삭제가 명시적으로 허용되고
  ORD 자동 정리의 범위·활성화가 검증됐다면, 이 비율 기준 대신
  실제 `available_kib`가 `expected_new_kib + minimum_reserve_kib`보다
  **커야 한다**는 별도 용량 예산으로 판정할 수 있다.
  예상 증가량과 예약 여유량은 각각 양수여야 하며, 값이 없으면 거부한다.

2026-10-29 후속 요청에서 **기존 녹화물 삭제 허용** 의사가 확인되었다.
따라서 `AUTO_DELETE_NOT_DISABLED`는 무조건적인 정책 차단이 아니며
실제 시험별 명시적 동의·ORD 정리 범위 확인·정확한 byte-space 예산을
충족하면 해제할 수 있다. 그러나 이 대화의 허용은 지금 즉시 임의 경로에
`rm`을 실행하거나 기타 설정을 삭제한다는 지시가 아니다.
별도 승인으로 전체 보드를 초기화하는 것과도 구분한다.

2026-10-29 UTC 03:45의 읽기 전용 실측에서
`pim-mp=0.6.3.2+jhw.camera19`, `df /mnt/sd_cam=90%`,
`ORD.disk_limit_per=90`, `ORD.disk_manage=true`,
`ORD.event_auto_remove=true`, `ORD.target_copy=true`,
`VCM.srt_enable=true`, 복구 저널 파일 0건이었다.
이 자료는 바이너리/소스 attestation이나 실행 예산이 아니므로
**실제 OHT COPY 요청은 전송하지 않았다**. 자세한 수치와 실행 ID는
별도 공유 인계의 E2E preflight 근거에 보관한다.

게이트를 모두 통과하더라도 E2E 성공 판정은 별도다. 같은 시험 구간의
**원시 OHT 요청·응답**, **VCM overlay 및 실제 SRT 본문에 반영된 유일한
시험 상태**, **종료된 영상·SRT의 파일 유형·크기·타임스탬프·내용**,
**ORD가 선택한 세션과 복사된 이벤트 산출물의 연결**을 독립적으로 대조한다.
기존 파일, `.part`, `.srt_done`, `.all_done`, COPY ACK만으로는 PASS를 내리지 않는다.
정해지지 않은 패킷 형식을 임의 생성하거나, 결과가 불명인 요청을 자동
재전송하지 않는다.


## ORD 자동 정리 범위 소스 감사 — E2E 실시 전 추가 차단 조건

2026-10-29에 읽은 제품 소스
`/home/jhw/ai/opencode/projects/pim-package-jhw/ord/tcpServer.cpp`
(SHA256 `35be2e4e0d54eda2b96ef5d6e0524d64e0d926272ffbdd15a1cbdbc687f3f497`)
의 `waitingDisk()`에는 다음 경로가 존재한다. 이는 **읽은 소스 코드의
정적 분석**이며 실보드의 설치 바이너리와 동일하다는 증거가 아니다.

| 제품 소스 위치 | 코드상 관찰한 동작 | E2E 영향 |
|---|---|---|
| 618–714행 | 녹화 루트의 파일 개수 초과 시 시간대별 recycle 이동, 파일명/형식 예외 시 `ls -pt`로 선택한 항목에 `rm -f` 폴백 | 선택 항목이 녹화 파일로만 제한된다는 근거가 부족 |
| 717–812행 | SD 관리 용량 초과 시 recycle 시간대 폴더를 재귀 삭제. recycle 대상이 없으면 녹화 루트의 파일을 지우는 폴백 | 지워질 파일의 실제 종류와 운영 구성 확인 필요 |
| 814–905행 | 이벤트 용량/개수 초과 + 자동정리 활성 시 이벤트 파일/시간창을 삭제하고, 잘못된 파일명 분기에서 `rm -rf` 폴백 | `event_auto_remove=true`만으로 안전한 삭제 대상 목록이 보장되지 않음 |

기존 녹화물 삭제를 허용한 것은 **명시적 동의**이며,
이 예외 경로의 정리 범위가 검증됐다는 뜻은 아니다.
현재 제품 소스에서는 `automatic_cleanup_scope_verified=True`를
입증할 근거가 충분하지 않으므로 **미확인으로 유지**한다.
실제 COPY 요청으로 이 분기들을 유발해 시험하지 않는다.
대상 범위 확인이나 제품 소스 수정이 필요하다면 정식 제품 개발·릴리스·
보드 적용 경계를 따르고, 과거 안전 검사에서 차단된 바이너리·활성 설정
조회 작업을 다른 명령으로 우회하지 않는다.

## 직접 복사형 이벤트 E2E 증거 검증

`oht_e2e_evidence.py`는 수집된 관측치를 **오프라인에서**
대조하는 순수 판정기다. 대상 보드 접속, OHT 메시지 전송, 녹화물 생성,
복사·삭제를 수행하지 않는다. 실제 사용 시 외부 수집기가 아래 증거의
원본 바이트·측정 시간을 독립적으로 확인해야 한다.

- 공식 임대와 사전 게이트 결과를 확인한 뒤 원시 COPY 요청 1회·응답 1회를
  독립 파싱하고 각 바이너리 프레임의 SHA256, command, 타입·기계 ID 검증
  결과를 기록한다. ACK만으로 성공 판정하지 않는다.
- 동일한 boot ID와 `YYYYMMDD_HHMM` 세션 키를 증거에 바인딩하고
  이벤트 전에 최종 이벤트 저장 폴더의 파일명 집합을 수집한다.
  빈 집합은 정상적인 측정값이지만 미측정(`None`)은 실패다.
- 선택된 채널별 **finalized 원본 영상과 event 복사 영상** 및
  **finalized 원본 SRT와 event 복사 SRT**를 확인한다.
  파일 크기는 양수, SHA256은 원본·복사본이 동일해야 하며,
  복사본 크기와 출처 경로도 일치해야 한다.
- 복사본은 이벤트 요청 이후, 시험 완료 이전에 갱신돼야 한다.
  시험 전에 존재했던 event 파일, `.part`, `.all_done`, `.srt_done`은
  성공 근거가 아니다. 녹화 원본은 COPY의 특성상 요청보다 먼저
  완성됐을 수 있으므로 **복사본에만 새로운 생성/갱신 시간**을 강제한다.
- 시험용 유일한 상태 토큰이 VCM overlay와 실제 SRT 본문에 함께
  확인돼야 한다. 메타데이터의 `srt_content_verified=True` 자체는
  독립적인 SRT 본문 읽기나 신뢰할 수 있는 수집기 동작을 대신하지 않는다.

이 판정기는 **바이트가 그대로 복사되는 direct-copy 분기만** 판정한다.
COPY TAIL 구간에서 결합·변환되는 파일은 출력 해시가 원본 하나와
다를 수 있으므로, 별도의 multi-source 파일 목록·원본 구간·결합 해시
검증 계약을 추가하기 전에는 이 판정기의 PASS를 기대하지 않는다.
오프라인 합성 fixture의 PASS는 실보드 이벤트/녹화 PASS가 아니다.
