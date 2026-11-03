# PIM 패키지 전체 시스템 End-to-End QA 수락 규칙

## 실제 목표

본 검증은 **각 앱 단독 기능 테스트가 아니라**, i.MX8MP에 설치된 실제
`pim-mp`와 연관된 카메라·센서·MAX9296·gstApp·Owner(cam-operate)·VSD·
ORD·VCM·Finalizer·저장·OHT 외부 연동이 설정을 읽고 동작하며,
오류를 감지·자동 복구한 후 정상 녹화로 돌아오는지 확인한다.

CI/pytest는 QA 소스 동작의 증거일 뿐 **설치된 제품의 시스템 PASS가 아니다**.
상태 문자열, PID, TCP handshake, ACK, .srt_done/.all_done marker, 오래된 파일
단독 확인을 **전체 E2E 합격으로 승격하지 않는다**.

## 실제 실행 순서와 단계별 판정

| 단계 | 시스템 관측·작용 | 필수 합격 증거 | 범위/전제 |
| --- | --- | --- | --- |
| S0 설치·서비스 | pim-mp 및 종속 바이너리/버전/실행 PID, systemd 종속성, 부팅 ID, 설정 및 활성 runtime | installed files+hash, unit 상태와 실제 프로세스, 동일 boot 설정 snapshot 및 source 대응 관계 | **읽기 전용**, 직접 설정 변경·재시작 없음 |
| S1 정상 녹화 파이프라인 | camera input→gstApp frame counters→VCM SRT→Finalizer→저장 | 모든 활성 채널 frame 증가, edgeconf-merged runtime 설정 일치, 같은 세션의 종료 MP4/SRT 및 ffprobe FPS·해상도·비트레이트, 실제 최신 파일 | **읽기 전용**. 원시 파일/FD/EOS/fsync 증거는 별도로 확보 |
| S2 설정 반영 | edgeconf/ORD/VCM 설정 변경→Owner 재구성→실제 camera/mux/파일 | 원본/변경 후 config+merged runtime, 소비 앱 인식, 실제 MP4·SRT/영상 광학 반영, 타 채널 부작용 없음, 원본 SHA exact restore | 공식 배타 board lease, 고정 프로파일, 독립 복구·writer fence 검증 후 물리 승인 |
| S3 외부 명령·앱 간 연동 | OHT 실제 요청→ORD·VCM IPC/overlay/SRT→COPY event media | raw 요청·응답, 동일 boot/session·상태 토큰, 최종 source/event 영상·SRT 크기·SHA/시간창 일치 | installed protocol/WRITE/COPY 위험과 삭제 범위 검토·승인 필요. ACK 단독 PASS 불가 |
| S4 저장관리 | MP4/SRT finalize, SD↔RAM fallback, ORD root rotate/recycle prune | 실제 파일수·사용률, closed writer FD·EOS, 삭제/이동 대상 scope와 처리 증거, 복구 후 recording continuity | 현재 90%/1,000개 **한도 판정**과 다음 45분 미래 headroom **분리** |
| S5 오류 검출·복구 | gstApp/ORD/VCM fault, SD unmount, 설정·네트워크 오류를 하나씩 주입 | 실제 fault 발생→감지 타임라인→Owner recovery→새 PID·frame counters→새 finalized MP4/SRT, HEALTHY, 원본 config/runtime 확인 | 사전 원본 SHA·자동 원복 보장, 실패 즉시 중단. 다른 fault 전파 금지 |
| S6 장시간 시스템 | 정상/복구 이후 지속 녹화와 ORD 용량관리·시스템 안정성 | 세션 누락·드롭·에러/CPU/RAM/SD drift, 원본 복구, 미회복 fault 없음 | 호스트 호출 최대 1,800초를 넘는 45분은 독립 지속 supervisor 및 별도 승인 |

## S0·S1 첫 실행 후보 (신규 코드)

- Plan: `profiles/plans/pim_system_live_readonly.yaml`
- 전제 순서: `config_integrity` → `verify_ord_vcm_integration` →
  `verify_pim_system_live_pipeline`
- 설정 변경, 재부팅, restart, fault injection, OHT 메시지 전송,
  SD 기존 미디어 삭제 없음. 세 case 모두 `setup`/`teardown` 미설정.
- `verify_pim_system_live_pipeline`:
  1. 설치 pim-mp 버전 및 ORD·VSD·VCM 바이너리 SHA256을 **관측**하고,
     각각의 실행 중인 단일 PID에 대해 `/proc/PID/exe`의 SHA256과 설치 파일의
     SHA256이 같아야 한다. 다르면 구버전 실행/설치 드리프트로 실패 처리한다.
     프로세스가 0개 또는 2개 이상이어도 모호하므로 FAIL이다.
     이 일치성은 실행본-설치본 동일성만 뜻하며 승인된 제품 소스/빌드 SHA와의
     대응은 별도 릴리스 검증이 필요하다.
  2. owner cam-operate, ord-operate, vsd-operate systemd와 필수 프로세스
     gstApp/BG_Check/chk_cam_operate/ord/vcm/vsd를 확인한다.
  3. edgeconf의 채널 enable/Flip/AE/AWB/BPS/FPS/해상도·녹화시간이
     실제 `/run/pim-camera/config/pim_runtime.json`에 반영됐는지 비교한다.
  4. FrameFlowCheck로 **4채널 중 실제 enable된 채널**의 frame counter
     두 시점 증가율과 edgeconf FPS를 비교한다.
  5. 현재 부팅의 gstApp `Session complete`에서 획득한 세션에 대해
     최근 5분의 finalized MP4를 활성 채널 각각 확인한다. ffprobe
     FPS·해상도·codec·duration·bitrate가 설정과 맞아야 한다.
     VCM `srt_enable=true`이면 같은 세션의 최종 SRT도 있어야 한다.
- 기본 케이스의 `known_issues` WARN 승격을 해제하고 실패 시 중단하며
  `threshold_pass_rate=1.0`을 요구한다. 최대 210초 monitor 중 준비 완료
  여부를 검사한다. 화면 반전/AE/AWB의 **실제 광학 효과**는 이 계획만으로
  입증할 수 없다. 완료된 파일 존재도 fsync/FD 종료 증거로 오인하지 않는다.

### 실행 승인 경계

실제 실행은 공식 `scripts/with_pim_board.sh`의 배타 lease와
명시적으로 등록·검증된 **읽기 전용** 실행 프로파일을 통해서만 한다.
아래는 운영 검토를 위한 **미실행 명령 예시**이다.

```bash
scripts/with_pim_board.sh --for 15m --purpose "PIM installed system S0/S1 read-only" -- \
    python3 -B pim_check.py --plan pim_system_live_readonly --host 192.168.214.4
```

기존 빌드서버의 `pim-check/main` 작업 트리에는 사용자 미커밋 변경이
있고 구버전 plan runner가 있을 수 있다. 따라서 이 Plan을 그대로 배포한 것처럼
간주하거나 기존 구버전 실행을 승인하지 않는다.
기존 MCP에 등록된 읽기 전용 프로파일은 **플랜 미리보기**에 한정되어
S0/S1 실보드 계측/출력 프로파일이 아니다. 이전 safety-denied source staging,
제품 patch/service 설치 경로를 generic command로 우회하지 않는다.

## 시스템 E2E 공통 최종 수락

- 한 실보드의 동일 설치 패키지 버전·바이너리 SHA·boot ID·채널·설정
  기반으로 모든 evidence를 묶는다. 다른 날짜의 과거 관측은 현재 PASS 아님.
- 시험별 S0 baseline과 S1 안정 상태를 먼저 확인하고, S2~S5는 한 번에
  하나의 설정·fault만 변경·복구한다. 별도 시험의 결과를 섞지 않는다.
- 제어 명령 전송 이후에 실제 소비 앱 처리/영상·SRT·이벤트 결과를
  시간·채널·세션과 함께 대조하고, 이후 원본 설정 정확 복구를 확인한다.
- 자동 복구가 아닌 수동 재시작은 자동 복구 PASS로 판정하지 않는다.
  복구 실패나 source mismatch, writer source 미확인 시 FAIL/BLOCK.
- 종료 후 JSON/JUnit/Markdown에 요청·응답, 설정 before/after SHA,
  package/source identity, 실제 미디어/오류 타임라인, 복구 측정값을 남긴다.
- G1 authentic Owner writer fence 및 G2 independent exact-original restorer는
  **S2/S3 위험 동작·S4 destructive path·S5·S6**의 선행 안전 조건이지만,
  기존 설정을 읽고 현재 동작을 관측하는 S0/S1을 무조건 차단하지 않는다.

현재 QA 코드 테스트 통과는 본 문서의 S0/S1 **실보드 PASS가 아니다**.
