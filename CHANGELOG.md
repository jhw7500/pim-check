# Changelog

## Unreleased (2026-08-23)

### stream 경로도 inject-only fault 케이스를 실행한다 (pim-check#77)

- **fix(stream)**: `StreamRunner._run` 이 `edgeconf_changes` 가 있을 때만
  `run_setup` 을 불러, inject-only 케이스(`fault_sd_unmounted`·`fault_gstapp_crash`)
  를 stream 으로 돌리면 **fault 가 주입되지 않은 채** "결함 없는 보드" 를 검사해
  무의미하게 PASS 했다. 실행 결정을 다른 4개 경로(cli/web/parallel/plan)처럼
  `run_setup` 에 위임한다 — run_setup 은 inject-only/ord-only 모드와 "이미 일치"
  skip 을 정식 지원한다.
- 같은 계열 부수 해소: edge 는 일치하는데 ord 만 다른 케이스가 stream 자체
  사전 체크(edgeconf 만 봄)에 걸러지던 것도 위임으로 함께 풀린다.
- phase 메시지 UX 유지 — 적용 전 "Applying N changes + reboot..." 예고, 일치 시
  "Config matches, skip reboot", inject/ord 전용 메시지 1건 추가. "Setup complete"
  는 실제로 적용됐을 때(run_setup 이 True)만 낸다.
- 가드: 위임 단언 3건(`tests/test_stream.py::TestSetupDelegationToRunSetup`) 추가,
  `tests/test_teardown_recovery.py` 의 stream 경로 테스트를 형제 경로와 같은
  inject-only 프로파일로 되돌림(#77 검증 방법 그대로 — 우회 제거).
- 자동 리뷰 반영(#99): ① setup 중 Ssh 예외(`SshTimeoutError`/`SshConnectionError`
  — `TimeoutError` 계열이 아님)가 나면 러너 스레드가 죽어 부분 주입 fault 가
  보드에 남고 done 없이 스트림이 매달리던 것 — 잡아서 복구를 시도한 뒤
  done(ERROR) 로 반드시 닫는다(Codex P1, 가드 3건). ② "Config matches, skip
  reboot" 예고가 edgeconf 만 보고 나가 ord 상이 결합 프로파일(720p_2ch 등)에서
  예고 직후 재부팅하던 것 — 예고 기준을 run_setup 과 같게 edge+ord 모두로
  맞춘다(Codex P2, 가드 2건). ③ teardown(복구·정상 말미 모두)이 목록 밖 예외를
  던지면 done 전달이 막혀 같은 매달림을 재현하던 것 — teardown 은 best-effort 로
  두고 어떤 예외도 done 을 막지 않게 넓힌다(Claude MEDIUM, 가드 2건).

### heartbeat 판정을 YAML 셸 16중복에서 checks/cam_state.py 로 (pim-check#93)

- **refactor(checks)**: `BG_Check watcher alive (cam_state touch <30s)` 셸 체크가
  16개 `multi_*.yaml` 에 복제돼 있던 것을 `CamStateCheck` 로 이관했다. AGENTS.md
  의 "체크 로직은 `checks/` 의 BaseCheck 서브클래스" 규약 위반이 원 지적
  (#84 PR #90 Codex P1). 이미 두 번(최초 도입, 시계 역행·오버플로 가드) 16곳
  일괄 치환을 반복한 동기화 부담도 함께 제거된다.
- **커버리지 순증**: cam_state 체크가 도는 **모든** 프로파일(generated/fault 포함)
  이 감시자 생존 검증을 자동으로 받는다 — 기존에는 multi 16개에만 있었다.
- **임계값 설정화**: `checks.cam_state.heartbeat_max_age_sec` (base.yaml, 기본 30)
  로 노출 — 명령 문자열 하드코딩이 아니라 프로파일 설정이 됐다. 설정 오타는
  크래시나 침묵 통과가 아니라 체크 FAIL 로 표면화된다.
- **판정·의미 보존**: NO_FILE / BAD_VALUE(비수치·자릿수>11) / NEVER_TOUCHED(0) /
  FUTURE(시계 역행) / STALE=\<N\>s 진단 구분과 "감시자 프로세스 생존 신호이지
  카메라 정상이 아니다"(카메라는 state·ch{N}_error 담당) 라는 신호 의미를 그대로
  옮겼다. timestamp 값과 보드 now(`date +%s`)를 **한 SSH 왕복**으로 읽어 호스트
  시계와 섞이지 않고, 수집 명령은 어떤 파일 상태에서도 exit 0 + 출력을 지킨다.
  관측 불가(수집 실패·형식 붕괴)는 NO_DATA 로 fail-closed.
- **가드 교체**: 기존 테스트가 YAML **형태**(16개 파일에 블록 존재)를 단언하던
  것을 판정표 단위 테스트 + "어떤 프로파일 셸도 timestamp 를 읽지 않는다" 는
  중앙화 가드로 바꿨다. 생성 명령은 지금도 실제 셸에서 돌려 exit 계약을 검증한다.

### plan 의 복구를 케이스 단위로 — 앞 fault 를 복구하지 않고 다음을 주입하던 것 (pim-check#95)

- **fix(plan)**: `recovery_command` 를 **케이스(시도)마다** 실행한다. 기존에는 플랜
  끝의 teardown 한 번뿐이었고 `last_teardown_cfg` 가 매번 덮어써져 **마지막 것만**
  실행됐다. 앞 케이스의 fault 가 복구되지 않은 채 다음 fault 가 주입된다.
- `fault_injection` 플랜이 정확히 그 형태다 — `fault_gstapp_crash` →
  `fault_sd_unmounted` 순이고 **둘 다 `recovery_command` 를 갖는다**. gstApp 이 죽어
  있는 상태에서 SD 언마운트 반응을 재는 셈이었다. `case_retry: 1` 이라 실패한 첫
  시도도 복구 없이 재시도됐다.
- **두 동작의 주기를 갈랐다**: 복구는 fault 해제라 재부팅을 수반하지 않으므로 케이스
  단위, 설정 복원(edge/ord)은 재부팅이 붙으므로 캠페인 끝(#68). 케이스별 복구는 빈
  setup 을 넘겨 복원을 건너뛰고, 캠페인 teardown 은 `recovery_command` 를 넘기지
  않아 마지막 케이스가 두 번 복구되지 않는다.
- `setup:` 이 없는 케이스는 `_run_single_case` 가 매니저를 만들지 않으므로 복구
  시점에 만든다 — 복구는 setup 유무와 무관하게 도달해야 한다(#75).
- **중단 경로 보존**(자동 리뷰 반영): SIGINT/SIGTERM 이 케이스 실행 도중에 오면
  `KeyboardInterrupt` 가 케이스별 복구 블록 **앞에서** 발생한다. 복구를 케이스로
  옮기면서 finally 에서 빼면 그 경우 **fault 가 보드에 남는다** — graceful shutdown
  핸들러가 존재하는 이유가 정확히 그 정리다. 미해제 복구를 `pending_recovery` 로
  추적해 finally 가 넘겨받는다(정상 종료면 이미 `None` 이라 중복 없음).
- 가드 4건: 케이스 순서(앞 복구가 다음 주입보다 먼저) · 실패한 시도도 복구 ·
  마지막 케이스 복구 1회 · 케이스별 복구가 재부팅을 유발하지 않음.

### cam_state 살아있음 판정을 heartbeat 로 — last_ok 는 이름과 실체가 반대였다 (pim-check#84)

- **fix(cases)**: `ch{N} cam_state last_ok freshness (<30s)` 체크 **36건(16파일)** 을
  걷어내고, 파일당 `cam_state heartbeat freshness (<30s)` **1건**으로 대체했다.
- 원래 결함은 "이름이 약속한 `<30s` 비교를 하지 않는다" 였다 — 파일이 있고 비어
  있지 않으면 OK 라, 한 시간 전 값도 지난 부팅 값도 통과했다.
- **그런데 시간 비교를 넣는 것이 답이 아니었다.** 보드 소스(`/opt/pim/lib/cam_state.sh`)
  를 보면 `last_ok` 를 쓰는 곳이 둘뿐인데, `cam_state_init`(초기값 `0`)과
  **`cam_channel_error()`** 다. 즉 값이 갱신되는 유일한 순간이 **에러 발생 시점**이고,
  정상 복구(`cam_channel_clear`)는 건드리지 않는다. 정상 운영 중에는 영원히 `0` 이다
  (보드 실측: 정상 상태에서 4채널 전부 `0`). `now - L < 30` 을 넣었다면
  **정상일 때 FAIL / 에러 직후 PASS** 로 뒤집혔을 것이다.
- 대신 보드가 실제로 갱신하는 신호를 본다 — `cam_state_touch()` 가 쓰는
  `/tmp/cam_state/timestamp`(보드 실측 delta **1s**). 채널별 상태는 이미 정상 동작하는
  `ch{N} cam_state error count` 가 담당하므로 중복되지 않는다.
- **이 신호가 무엇인지**: `BG_Check_for_pim.sh` 의 1초 루프가 정상·에러·grace **모든
  분기에서** touch 한다. 즉 상태와 무관한 **감시자 프로세스 생존 신호**이지 "카메라가
  정상이다" 가 아니다(실측 delta 1s 는 관측이 아니라 이 설계의 결과). 그래서 체크
  이름을 `BG_Check watcher alive (cam_state touch <30s)` 로 두어 오독을 막았다.
  카메라 정상 여부는 `state`(healthy)와 `ch{N} cam_state error count` 가 본다.
- **잔여 공백(의도적)**: 채널별 *생존* 신호는 이제도 없다 — `ch{N}_error=false` 는
  "에러가 기록되지 않았다" 이지 "최근에 확인됐다" 가 아니다. 다만 `last_ok` 36건이
  그 커버리지를 주고 있던 것도 아니므로(에러 때만 움직였다) 이 교체는 손실이 아니라
  순증이다. 구멍 자체는 #91(FW)·#85 자리다.
- **시계 역행·오버플로 가드**(자동 리뷰 반영): `D` 가 음수면 `D < 30` 을 만족해
  **정지한 writer 가 계속 healthy 로 보인다**(재현: 미래 timestamp → OK). 또 숫자로만
  이루어졌지만 셸 정수 범위를 넘는 값이 `case` 가드를 통과해 `-eq` 에서 dash 가
  죽어 **exit 2 + 무출력** 이 된다(재현: 25자리 → `ssh.run` 이 None). 자릿수 상한과
  음수 판정을 넣어 둘 다 막고, `FAIL:FUTURE=<N>s` 로 따로 진단한다.
- 명령을 **block scalar(`|-`)** 로 바꿨다. YAML 이스케이프가 사라져 `\n` 오염이
  구조적으로 불가능해지고 흐름(존재→정제→수치→범위→시간)이 한눈에 보인다.
- 새 체크는 `0`(초기값·미갱신), 비수치, 빈 파일, 파일 부재를 각각 다른 진단으로
  가른다(`FAIL:NEVER_TOUCHED` / `BAD_VALUE` / `NO_FILE` / `STALE=<N>s`). exit 0 +
  항상 출력 규약 준수.
- 가드(`tests/test_cam_state_heartbeat.py`): `last_ok` 잔존 0 · 파일당 heartbeat 정확히
  1건 · 명령을 **실제 셸에서** 6가지 입력으로 돌려 판정 확인. 보드에서도 OK 실증.

### plan 이 캠페인 시작 전 상태로 복원한다 (pim-check#68)

- **fix(plan)**: plan 은 케이스마다 teardown 하지 않고 끝에서 한 번만 정리하므로,
  복원 도달점이 "플랜 시작 전"이 아니라 **"마지막 케이스 직전"** 이었다. 25 케이스짜리
  `comprehensive` 를 돌리면 보드는 24번째 케이스 설정을 안고 끝난다.
- 파일별 **최초** 스냅샷을 캠페인 내내 보관하고 종료 시 그것으로 되돌린다
  (`SetupManager.adopt_snapshots`). "첫 케이스"가 아니라 **"그 파일을 처음 건드린
  케이스"** 기준이라, 첫 케이스가 만지지 않은 파일도 제대로 회수된다.
- 복원 **대상**도 캠페인 기준으로 넓혔다 — `ord_vcm_changes` 를 쓰는 케이스가 6건
  있어, 중간 케이스가 ord_vcm 을 바꾸고 마지막이 edgeconf 만 바꾸면 ord_vcm 이
  되돌려지지 않았다.
- 기존 `.bak` 방식으로는 불가능한 수정이다(보드의 단일 슬롯을 config_guard 가 부팅마다
  갱신). #67 의 호스트 스냅샷이 호스트 메모리에 있기에 가능해졌다.
- **진입 조건도 캠페인 기준이다** — 복원 대상과 `reboot_after` 를 넓히고도 "복원을
  시작할 것인가" 를 마지막 케이스로 판단하면, 설정을 바꾸지 않는 케이스로 끝나는
  플랜에서 앞선 케이스들의 설정이 통째로 남는다. **보드 실행이 이 구멍을 드러냈다** —
  `smoke`(8케이스, `config_integrity` 로 끝남)를 돌리자 플랜 종료 후 `PIM_CHECK`
  로그가 0건이었고 edgeconf 는 캠페인 시작 전이 아니라 `fhd_4ch` 의 값으로 남았다.
- **복원은 되돌릴 원본이 있을 때만 한다** — 전 케이스가 setup-skip 이면 `changes` 는
  누적돼도 스냅샷은 안 찍힌다. 그대로 복원하면 보드 `.bak` 폴백으로 떨어져 바꾸지도
  않은 설정을 되돌리고 재부팅까지 낭비한다. #75 리뷰에서 4개 실행 경로에 적용한 것과
  같은 논리를 plan 캠페인 경로에도 적용했다.
- 가드: `tests/test_plan_campaign_restore.py` — 실제 `SetupManager` 로 3케이스 플랜을
  돌리고 **보드로 나간 복원 페이로드**를 캡처해 캠페인 최초 상태인지 확인한다.
  뮤테이션 2종(스냅샷 인수 제거·ord 대상 확장 제거)으로 확인했다.

### fsync 검사 소스를 kern.log 로 — 링버퍼 의존 마지막 22건 해소 (pim-check#69)

- **fix(cases)**: `dmesg` 를 읽던 **fsync 체크 21건**과 `dmesg --level=` **1건**을
  `/var/log/cantops/kern.log` 로 이관했다. #74 가 fault 체크 11건을 옮겼고, 이번에
  남은 22건을 마저 옮겨 **케이스에서 링버퍼 의존이 사라졌다**.
- **fix(setup)**: readiness 게이트(`camera_init`)의 probe 도 같은 소스로 옮겼다.
  게이트가 `dmesg` 로 통과한 직후 체크가 `FAIL:NO_DMESG` 로 떨어지는 조합이 실제로
  관측됐는데(같은 소스가 그 사이에 비었다), 이제 게이트와 체크가 같은 파일을 본다.
- **⚠ 이관이 만드는 새 위험을 함께 처리했다** — `dmesg` 는 부팅마다 비워져 "앵커 0 =
  이번 부팅"이 공짜로 성립했지만 `kern.log` 는 재부팅을 넘어 산다(4월치까지 `.gz`).
  그대로 옮기면 **과거 부팅 마커까지 세어 게이트가 즉시 열린다.** 부팅 경계를
  **monotonic 감소 지점**으로 잡아 마지막 부팅 구간만 센다.
- **feat(setup)**: 앵커 파서가 타임스탬프를 하나도 못 읽으면(`p=0`) 총건수 폴백이
  **조용히** 발동하던 것을 1회 경고로 드러낸다 — (d). 폴백이 걸리면 #66 의 앵커
  델타가 통째로 무효화되는데 로그가 정상 경로와 똑같아 보였다.
- **feat(cases)**: `FAIL:NO_DMESG` 를 **`FAIL:NO_SOURCE`(소스 자체가 없음)** 와
  **`FAIL:NO_MARKER`(소스는 있는데 이번 세션 마커가 없음)** 로 갈랐다 — (c). 예전에는
  둘이 같은 실패로 보여, 보드에서 커널 로그가 통째로 사라진 사고를 케이스 결함으로
  오인할 뻔했다.
- **가드** — 형태 목록을 늘리는 대신 **실행으로 확인한다**:
  `tests/test_kernel_log_source.py` 에 21건을 실제 셸에서 돌리는 5종 시나리오(소스
  없음 / 마커 없음 / 일치 / 불일치 / **과거 부팅 줄**)를 추가했고,
  `tests/test_fsync_probe_source.py` 는 probe 의 awk 를 임시 kern.log 에 대고 돌려
  t/p/n 을 직접 검산한다. 뮤테이션 2종(부팅 경계 제거·진단 분기 제거)으로 확인.
- `KERN_LOG_PATH` 상수를 `setup.py` 에 두어 테스트가 경로를 치환해 실행할 수 있다.

### teardown 이 setup 의 readiness 기대를 승계하던 것 (pim-check#70)

- **fix(setup)**: `run_teardown` 진입 시 readiness 기대값(`_ready_fsync`,
  `_ready_ae_targets`, `_ready_processes_list`, `_ready_recording_paths`)을 비운다.
  이 값들은 **방금 끝난 케이스**에서 유도된 것인데 teardown 은 설정을 **복원한 뒤**
  재부팅하므로, 복원된 보드를 복원 전 기대값으로 게이팅하고 있었다.
- AE 정착은 gstApp 기동 +16s 가 필요한데 teardown 예산은 20초 고정이라 **들어갈 수
  없었다** — 5개 실행 경로 전부가 매 실행 끝에 20초를 버리고 `[timeout] ae_settle`
  경고를 찍었다. 이제 teardown 재부팅은 `ssh` 단계만 기다린다.
- `_config_snapshots`(복원 원본)는 그대로 둔다 — 비우면 teardown 복원이 죽는다.
  그 성질을 테스트로 못박았다(`test_restore_source_survives_the_reset`).
- 가드: `tests/test_teardown_readiness.py` 신규 4건. 승계가 실재한다는 **전제**부터
  확인한 뒤(`ssh, session_anchor, processes, camera_init, ae_settle, recording`),
  teardown 재부팅 시점의 단계 목록을 캡처해 `["ssh"]` 임을 단언한다. 뮤테이션
  (초기화 호출 제거)으로 가드가 실제로 잡는 것을 확인했다.

### teardown.recovery_command 가 실행되지 않던 것 — fault 주입이 복구 없이 남았다 (pim-check#75)

- **fix(setup)**: `run_teardown` 이 `setup:` 섹션에서만 `recovery_command` 를 읽었는데,
  이 키를 쓰는 케이스 **2건이 모두 최상위 `teardown:` 아래**에 두고 있었다
  (`fault_sd_unmounted`, `fault_gstapp_crash`). 그래서 **주입은 되고 복구는 안 되는**
  상태가 계속됐다 — `fault_sd_unmounted` 는 `/mnt/sd_cam` 이 언마운트된 채 남는다.
  로그는 `teardown DONE — inject-only recovery` 로 찍혀 정상처럼 보였다.
- `run_teardown(setup_config, teardown_config=None)` 로 바꿔 **`teardown:` 을 정본**으로
  읽고, `setup:` 쪽도 계속 읽어 하위 호환을 지킨다(둘 다 있으면 teardown 만 실행).
- **실행 경로 5개 전부** 전달하도록 고쳤다 — cli(`pim_check.py`) · `web.py` ·
  `stream.py` · `parallel.py` · `plan.py`. plan 은 마지막 케이스 설정을 자기
  지역변수로 들고 종료 cleanup 을 돌기 때문에 `last_teardown_cfg` 추적을 새로 추가했다
  (#67 에서 plan 만 다른 매니저를 쓰다 수정이 통째로 무효화된 전례와 같은 계열).
- **feat(config)**: `load_profile` 이 `teardown:` 아래 **읽히지 않는 키**를 만나면 경고한다.
  이 버그의 본체는 "키를 엉뚱한 섹션에 뒀는데 아무도 말해주지 않았다" 이므로,
  같은 착각이 다른 키로 재발하는 것을 로드 시점에 드러낸다.
- **가드 2층** (`tests/test_teardown_recovery.py` 신규, `test_plan_execute.py` 보강):
  ① 단위 — teardown 섹션의 recovery 가 실제로 보드 명령까지 나가는지 ssh 캡처로 확인.
  ② 경로 — 5개 경로가 각각 그 섹션을 전달하는지 경로별로 못박음. plan 은 실제
  `SetupManager` 로 한 바퀴 돌려 복구 명령이 나가는지 직접 본다. 뮤테이션 2종
  (plan·cli 되돌리기)으로 가드가 실제로 잡는 것을 확인했다.
- **인접 결함 발견(이번 범위 밖)**: `stream.py` 는 `edgeconf_changes` 가 있을 때만
  `run_setup` 을 부르므로 **inject-only fault 케이스는 주입도 복구도 하지 않는다.**
  별도 이슈로 분리했다.

## Unreleased (2026-08-21)

### 커널 로그 체크 소스 이관 — 구조적 거짓 PASS 해소 (pim-check#73, #71)

- **fix(cases)**: `journalctl -k` 를 읽던 fault 체크 **11건**을 rsyslog 의
  `/var/log/cantops/kern.log` 로 이관. 이들은 **구조적으로 실패할 수 없었다** —
  소스가 사실상 비어 있고(보드 실측: `journalctl -k` **31줄** vs kern.log
  **56,140줄**) `expected: "0"` 이라, 결함 발생 여부와 무관하게 PASS 했다.
- **증명적으로 무효였던 3건** (kern.log 매칭 vs journalctl 매칭):
  `fault_cam_disconnect` **540 vs 0** · `fault_i2c_bus_error` **540 vs 0** ·
  `board_error_detect` **3978 vs 0**. 특히 `fault_cam_disconnect` 는 카메라 단절을
  **절대 검출하지 못했다** — fault 케이스가 그 fault 를 못 잡으면 존재 이유가 없다.
- **왜 kern.log 인가**: rsyslog 가 `kern.notice`(severity 0–5)를 이 파일로 보내고,
  max9296 의 fps/오류 출력이 `printk(KERN_NOTICE)`·`KERN_ERR` 라 **설계상 보장**된다
  (측정이 아니라 소스로 확인). 링버퍼가 아니라 파일이라 `SYSLOG_ACTION_CLEAR`·wrap
  양쪽에 면역이고, 로테이션도 있다. 왕복 0.07~0.6s.
- **스코핑 축을 명시**했다. kern.log 는 재부팅을 넘어 살아남으므로(4월치까지 보존)
  필터가 **유일한** 부팅 스코핑이다:
  - 관찰형 체크 9건 → **세션 앵커**(`/tmp/pim_check_anchor`, `uptime -s` 폴백).
    케이스들이 `local0.log` 에 이미 쓰는 `substr($0,1,19) > bt` 패턴과 동일.
  - `fault_rtc_fail` → **monotonic**(`[   25.557314]` 필드, 부팅 경계 = 값 감소).
    이 체크의 **가설이 "RTC 통신 실패"** 라 시스템 시계를 신뢰할 수 없다 — wall-clock
    필터는 자신이 검출하려는 결함에 의해 망가진다. **이로써 #71 도 해소된다**
    (기존엔 시간창이 아예 없어 영속 저널 전체를 훑었다).
  - `fault_sd_unmounted` → 세션 앵커(주입 케이스지만 inject-time 앵커 기계가 없어
    부팅 스코프로 폴백).
- **fix(cases)**: `board_error_detect` 의 패턴에 BSP 부팅 잡음 제외를 추가.
  `error|fail|fault` 는 정상 부팅 로그에도 매칭돼(28건) 이관 즉시 상시 FAIL 이 된다.
  pim-check 소관 밖 주변장치(오디오·HDMI·PCIe·WiFi·SPI)의 probe 실패를 제외하면
  **2건**만 남는데, 그 2건이 **실제 카메라 i2c 오류**다(아래 참조). 과거 진짜 오류
  540건은 제외 필터를 그대로 통과함을 확인했다.
- **fix(cases)**: `fault_sd_unmounted` 의 `expected_min: 0` → **1**. `on_fail` 이
  "감지 안 됨" 인데 임계가 0 이라 **아무것도 감지 못해도 통과**했다(소스와 무관한
  별개 결함).
- **보드 검증**: 이관한 11건을 실제로 실행해 10건 PASS / `board_error_detect` 만
  FAIL(2). 그 FAIL 이 **정답**이다 — 이 보드에 지금 카메라 i2c 오류 2건이 있다:
  `[I2C:2][max9296.c:1197] ch0 MCP4018(0x2f) write fail` ·
  `[I2C:2][max9296.c:2679] ch0 dual applied fail (ret=-6)`.
  **두 카메라 전용 체크는 이걸 놓친다** — 패턴이 `error` 만 보고 `fail` 을 안 본다.
  패턴 정제는 #73 의 후속 항목으로 남긴다.

### teardown 복원을 호스트 스냅샷으로 (pim-check#65)

- **fix(setup)**: teardown 의 config 복원이 **조용히 no-op** 이던 것을 고친다.
  기존에는 보드의 `/root/shared_v/backup/*.bak` 에서 복원했는데, 그 슬롯은
  pim-check 전용이 아니라 보드 FW `config_guard.sh` 의 known-good 자리다.
  guard 가 **부팅 시 valid 한 현재본을 그 자리로 복사**하므로,
  "설정 적용 → 재부팅" 을 거치면 `.bak` 이 이미 케이스 설정으로 덮여 있어
  `restore()` 가 아무것도 되돌리지 못했다 (보드 실측: 케이스 실행 직후 live 와
  backup 이 둘 다 그 케이스 설정).
- **feat(setup)**: `snapshot_config` / `restore_from_snapshot` 신설 — 복원 원본을
  **호스트가 들고 있는다**. 보드 경로 계약이 없어 guard 와 구조적으로 경합하지
  않는다. 전송은 base64(설정이 JSON 이라 셸 인용 사고 회피), 되돌릴 때는 임시
  파일에 풀고 `jq -e .` 로 검증한 뒤에만 원자적으로 `mv` 한다 — 깨진 설정을
  제자리에 쓰면 다음 부팅에서 guard 가 디폴트 리셋을 트리거한다.
- 스냅샷은 **변경 전에** 찍는다(변경 후면 스냅샷이 케이스 설정이 돼 무의미).
  실패해도 setup 을 중단하지 않고 경고만 남긴다 — '복원 불가'는 종전과 같은
  상태이고, 케이스를 죽이는 편이 더 나쁘다. 스냅샷이 없으면 기존 `.bak` 경로로
  폴백한다.
- `backup()` 의 `.bak` 쓰기는 **그대로 둔다** — 그건 guard 가 디폴트
  (`/etc/defaultconf.json`) 리셋을 막는 데 쓰는 별개 용도다. FW 계약 불변.
- **fix(plan)**: plan 모드의 teardown 이 `setup_factory` 로 **새 매니저**를 만들어
  스냅샷(인스턴스 속성)이 빈 채로 시작하던 것을 고친다 — 그대로면 plan 경로에서
  이 수정이 **효과 0** 이다(항상 `.bak` 폴백). `_run_single_case` 가 만든 매니저를
  `mgr_holder` 로 노출해 `finally` 가 같은 인스턴스를 재사용한다(ssh 가 갈리면
  기존 factory 폴백 유지). `--case`·parallel·stream·web 은 원래 동일 인스턴스라
  영향 없고 **plan.py 만 예외**였다. #65 가 보고한 증상이 실제로 발현하는 경로가
  plan 모드라 이 수정이 없으면 PR 전체가 무의미해진다.
- 왜 중요한가: 복원이 안 되면 **케이스 결과가 실행 순서에 의존**한다. 실측상
  `edgeconf_changes` 를 가진 케이스 27개가 쓰는 키 62개 중 **모든 케이스가 공통
  으로 설정하는 키는 0개** — 어떤 키든 일부 케이스에서만 설정된다. 가장 좁은
  실제 경로는 `capture.enable` 미설정 6건(`verify_*`)이 직전 케이스 값을 상속하는
  것이다(`--include-generated` 로 `gen_*_cap_on` 이 먼저 돌면 발현).
- 스냅샷은 저장 전에 **실제로 디코드해서** 검증한다(`b64decode(validate=True)` +
  `json.loads`). 문자집합만 보는 검사는 길이가 4 의 배수가 아닌 절단 출력을
  통과시키고, 그러면 복원 때 `base64 -d` 가 죽어 조용히 `.bak` 폴백으로 떨어진다 —
  이 기능이 없애려던 경로다. JSON 파싱은 복원측 `jq -e .` 와 대칭이다.
- 폴백 로그를 성격별로 가른다: **스냅샷 없음**(setup-skip 등 정상)과 **스냅샷은
  있는데 복원 실패**(이상 — 조용한 no-op)를 구분해 후자에만 `WARNING` 을 찍는다.

### 세션 앵커 도입 — 하드리셋 전환의 잔여 관문 2건 해소

`cam_hard_reset.sh`(pim-package-jhw#46) 로 케이스 간 재부팅을 대체하려면 "재시작 =
재부팅" 전제에 기댄 두 곳이 먼저 리셋 인지형이 돼야 한다. **오늘 동작은 그대로
보존하면서 앵커만 명시화**한다 (재부팅 경로에서 동작 불변).

- **fix(setup)**: 카메라 init(fsync) 게이트를 **앵커 이후 델타 판정**으로 교체.
  기존에는 `dmesg | grep -c` 로 존재만 봤는데, 그건 "링버퍼가 부팅마다 초기화된다"에
  의존한다. 하드리셋은 SoC 를 재부팅하지 않아 링버퍼가 안 비워지므로 **직전 부팅의
  fsync 라인이 게이트를 즉시 열어버린다**(카메라 init 전). 이제 awk 한 패스로
  `t=총건수 p=타임스탬프파싱 n=앵커이후` 를 세고 `n` 으로 판정한다
  (`_dmesg_anchor_uptime`, 재부팅 경로 기본 0 → 기존과 동일).
  printk 타임스탬프가 꺼진 보드(p=0)에서는 총건수로 폴백한다.
- **feat(setup)**: **세션 앵커 파일**(`/tmp/pim_check_anchor`) 신설 + readiness 단계
  `session_anchor` 를 `ssh` 직후에 추가(카메라 케이스만). 1행 = 앵커 시각,
  2행 = 기록 시점의 `uptime -s`. 세션 시작마다 **무조건 다시 쓴다** — 조건부로
  건너뛰게 만들면 하드리셋(같은 부팅 안의 새 세션)에서 이전 세션 앵커가 남는다.
  2행 대조는 writer 가 이번 부팅에 돌지 않은 경로(비카메라 케이스, 설정 일치로
  setup skip)를 위한 방어다. 참고로 이 보드는 `/tmp` 가 tmpfs 는 아니지만
  tmpfiles.d 의 `D /tmp` 를 systemd-tmpfiles 가 적용해 **부팅마다 비운다**(실측).
- **fix(cases)**: 카메라 케이스 5개의 부팅 앵커 **38곳**을 세션 앵커 경유로 교체
  (`fhd_4ch` 10, `720p_4ch` 10, `fhd_3ch_012` 8, `fhd_2ch_03` 5, `720p_2ch` 5).
  기존 `BOOT=$(uptime -s)` → 앵커 파일을 읽고 없거나 stale 이면 `uptime -s` 폴백.
  하드리셋은 `uptime -s` 를 바꾸지 않으므로 그대로 두면 **직전 케이스의 녹화 세션이
  매칭**돼 이전 케이스 영상으로 fps/bitrate/duration 을 검증하게 된다.
- **바꿀 곳이 한 곳으로 모였다**: 하드리셋 도입 시 `_write_session_anchor` 가 리셋
  시각을, `_dmesg_anchor_uptime` 이 리셋 시점 uptime 을 쓰면 되고 케이스 38곳은 불변.
- 새 케이스가 `BOOT=$(uptime -s)` 관행으로 되돌아오는 것을 막는 코퍼스 속성 테스트
  추가 (`TestCasesUseSessionAnchor`).

### AE 정착 readiness 게이트 (pim-check#61)

- **feat(setup)**: 리부트 후 안정화 readiness 에 `ae_settle` 단계 신설 —
  `ssh → processes → camera_init(fsync) → **ae_settle** → recording`.
  카메라 init 이후에도 AP1302 AE 레지스터는 전이값(AE_CTRL `0x029c`,
  AE_GAIN `0x0100`)을 거쳐 최종값에 도달한다. 콜드 기동 실측(2026-08-21,
  유선 192.168.214.4): 정착 = uptime 28.2s = **gstApp 기동 +16~17s**.
  기존에도 통과하긴 했으나 그 마진(+20~35s)은 체크 실행 순서와 readiness 통과
  시각에 의존하는 우연적 배치였다 — 명시 게이트로 고정한다.
  하드리셋 도입(pim-package-jhw#46)으로 부팅이 단축되면 붕괴하는 마진이라
  그 활용의 선결 과제였다.
- **판정 기준**: 케이스 기대값과 **일치하는** 읽기가 3초 이상 간격으로 2회
  (`PIM_AE_SETTLE_GAP_SEC`). '연속 2회 안정'이 아닌 이유는 전이값 `0x0100` 이
  3초 이상 유지돼 안정 기준만으로는 조기 통과하기 때문. 하한 앵커는 gstApp
  경과 16초(`PIM_AE_SETTLE_GSTAPP_ETIME_SEC`) — boot 이 아니라 gstApp 기준이라
  부팅 단축과 무관하다.
- **기대값 출처**: `setup.edgeconf_changes` 단일 소스(`ae_settle_targets()`).
  케이스가 **명시한** 값만 단언한다 — 미명시 키는 보드 잔존값(config 드리프트)이라
  기대값을 만들 수 없다. `enable: true` 채널의 `ae_on` → AE_CTRL(0x5002),
  `ae_on: false` + 정수 `ae_gain` → AE_GAIN(0x5006). auto 채널 gain 은 FW 재량이라
  단언하지 않는다. 타겟이 없으면 단계 자체가 붙지 않고,
  `camera_init_required: false` opt-out 은 AE 게이트에도 함께 적용된다.
- **fix(setup)**: ISP readback 주소를 **버스의 활성 채널 수**로 분기 —
  버스당 1채널이면 `0x3c`, 2채널이면 짝수 `0x11` / 홀수 `0x12`
  (드라이버 `dual ? AP1302_CH{0,1}_I2C_ADDR : AP1302_I2C_ADDR`).
  버스 단위 분기라 총 2채널이라도 버스당 1채널인 구성(`fhd_2ch_03` 형태)은
  양쪽 다 `0x3c` 다. 주소를 고정하면 dual 에서 두 채널이 같은 값을 읽어 오탐하고
  single 에서는 무응답으로 게이트가 열리지 않는다. 프로파일 코퍼스 readback
  249건 전건 일치로 확인했고, 케이스 yaml 의 실제 주소와 상시 대조하는 속성
  테스트로 고정했다.

### 배포 조합 sync — max9296 2.5 · gstApp camera-health (`docs/03-analysis/deploy-sync-2026-08-21.md`)

- **fix(setup)**: 카메라 readiness 의 dmesg fsync 마커를 ERE(`FSYNC_MARKER_RE`)로 교체.
  드라이버 2.5 부터 로그가 `max9296_fsync <mode> fps :` 로 바뀌어 구 리터럴은 0 매칭
  → 카메라 케이스 준비 게이트가 열리지 않던 파손 수정 (구/신 포맷 모두 매칭).
- **feat(checks)**: `max9296_abi` 신설 — modinfo version(기본 2.5) + prepare 상태라인
  (errno/worker_errno=0, state≠FAILED) + health_raw JSON(deserializer OK, enable 채널
  link up, serializer OK). 카메라 on/off 무관 상시 유효.
- **feat(checks)**: `cam_health` 신설 — gstApp 내장 camera-health v1 producer 스냅샷
  (`/run/pim-camera/gstApp.json`) 신선도(stale_ms, boot_id 대조) + FAIL observation 0.
- **base.yaml**: 두 체크 기본값·retry_policy 추가, `logs.error_patterns` 에
  `\[MAX9296_PREPARE\]`(gstApp prepare 실패 LOG_CRIT, 태그 앵커) 추가.
- **fault_gstapp_crash · process_restart_smoke**: gstApp 을 pkill 하는 두 케이스에서
  kill/respawn 구간 오탐 방지로 `cam_health`·`max9296_abi` 비활성화.
  fault_gstapp_crash 는 회복 감시 유닛도 실존하는 `cam-operate.service` 로 정정
  (2026-06 백로그 #2 전제 변경 — chk_cam_operate.sh 가 유닛으로 승격됨).
- **리뷰 반영 강화**: 체크 예외 유출 차단(JSON 모양 가드), 음수 age 상한(1s),
  uptime 불능 표면화, 부팅 직후 grace(NEED_PRODUCER_SNAPSHOT stabilization 토큰),
  fsync mode open set(`( [a-z-]+)?`), errno 정수 비교, 0ch deserializer 단언 스킵.
- **fix(cases)**: 케이스 custom_commands 의 fsync fps 추출 grep 21건
  (profiles/cases/*.yaml)도 동일한 구 리터럴 파손 — smoke 타겟 검증(1차)이 적발,
  `( [a-z-]+)?` ERE 로 일괄 교체. smoke 1차에서 신규 cam_health 가 실제 4채널
  GSTREAMER_SOURCE_STALL 을 포착(참양성)한 것도 실증됨.
- **fix(cases) 백로그 스위프 (21파일)**: ① manual gain 8192→512 — 실효 시
  센서 ≈16× 백색 포화로 bitrate 검증과 충돌 (근본 원인: max9296#26 의
  라이프사이클 race, `docs/03-analysis/deploy-sync-2026-08-21.md` §4)
  ② AE_GAIN(0x5006) 기대값 갱신 + smoke 3케이스에 readback 신설
  ③ bitrate 체크 보유 19파일에 `enc:"h265"` + 채널별 qp/profile [0,0]
  명시 고정 — 보드 edgeconf 인코더 드리프트로 인한 검증 비결정화 차단.
- Phase 0 보류 항목(외부 producer 2종·aggregator·manifest sha)은 분석 문서에 기록.

## v2.1.0 (2026-05-27)

### Multi-target viewer 시리즈

`pim_web_viewer.py` 단일 host 만 지원하던 web viewer 를 N개 host 동시 진행 + per-host detail 드릴다운으로 확장.

- **Per-target event 라우팅** (#38)
  - `events/by-target/<slug>/` 디렉터리 구조 + `events/active.json` 인덱스
  - `host=None` 인 legacy 경로 (`events/<file>.jsonl` + `events/current.jsonl`) 호환 유지
  - `threading.Lock` + `fcntl.flock(LOCK_EX)` 로 single/cross-process race 차단 (POSIX 한정 — Windows fallback 은 후속 PR 예정, `run_stream.py` line 27–29 에 `_fcntl = None` import 가드 이미 존재)

- **Web API multi-target endpoint** (#40)
  - `GET /api/active` — 현재 진행 중 host 목록
  - `GET /api/events?host=<slug>` — 특정 host 이벤트 스트림
  - `POST /start {targets: [...]}` — N개 host 동시 spawn
  - `POST /stop {host}` 또는 `{targets: [...]}`

- **Multi-column UI** (#41)
  - CSS grid `auto-fit` 으로 host 수만큼 컬럼 자동 분할
  - `tickMulti` 1.5s 폴링 + Stop/Start UX
  - 단일 host 진행 시 기존 single-view 유지

- **Column click → detail view** (#43)
  - 컬럼 클릭으로 legacy single-view 가 해당 host 로 전환 (사용자 피드백 반영)

- **JS escape 안전성** (#42, #44)
  - `INDEX_HTML = r"""..."""` raw string 채택 (#44) — Python ↔ JS escape 함정 영구 차단
  - Hotfix (#42): `split('\\n')` → `split('\\\\n')` (Python string literal 기준 — 실제 JS 출력은 `split('\n')` → `split('\\n')`) 이중 escape 누락으로 script 전체 parse 실패하던 회귀 수정
  - `tests/test_viewer_js_smoke.py` Playwright headless 로 모든 JS 함수 `typeof` 검증 (CI `js-smoke` job)

## v2.0.0 (2026-04-06)

대규모 고도화. 사내 QA 도구에서 완전한 테스트 자동화 플랫폼으로 확장.

### 새 기능

- **Schema-driven 케이스 자동 생성** (`--generate`)
  - 7개 축: resolution, channels, fps, hflip, capture, ord_disk, vcm_srt
  - 20개 케이스 자동 생성, 수동 케이스 중복 자동 skip
  - `--validate-schema`로 스키마 유효성 검증

- **웹 대시보드** (`python3 web.py`)
  - 수동/자동 테스트 실행, 태그별 일괄 실행
  - 케이스 상세 페이지 + SVG 추이 차트
  - 다크모드, Basic Auth (`--auth`)
  - Prometheus `/metrics` 엔드포인트

- **리포트 시스템**
  - `--html`: 자체 완결형 HTML 리포트
  - `--history`: JSONL 히스토리 누적
  - `--history-report`: 대시보드 HTML 생성
  - `--export-csv`: CSV 내보내기
  - `--compare`: 최근 두 실행 결과 비교 (PASS/FAIL 변화 감지)

- **병렬 실행** (`--parallel`, `--targets`)
  - ThreadPoolExecutor 기반 다수 타겟 동시 테스트
  - `profiles/targets.yaml` 타겟 목록 + 타겟별 overrides

- **알림**
  - `--webhook`: Slack/Discord webhook
  - Email 알림 (smtplib, `~/.pim-check.yaml` 설정)

- **운영 도구**
  - `--dry-run`: 재부팅 없이 설정 차이 미리보기
  - `--watch`: 연속 모니터링 + 대시보드 자동 갱신
  - `--log`: 실행 로그 파일 저장
  - `--init-config`: 사용자 설정 파일 생성
  - `--quiet`: 출력 최소화 (CI 친화적)
  - `--diff-targets`: 두 타겟 간 edgeconf 비교

- **케이스 관리**
  - `tags`: 케이스 태그 필터 (`--tag smoke`)
  - `depends_on`: 케이스 의존성 순서 보장
  - `known_issues`: FAIL → WARN 자동 전환 (exit code 0)
  - `retry_policy`: 체크별 SSH 재시도 횟수

- **확장성**
  - `checks/plugins/`: BaseCheck 서브클래스 자동 로드
  - Windows 지원 (paramiko + sshpass 폴백)
  - Docker 지원 (`Dockerfile`)
  - `pip install pim-check` (`pyproject.toml`)

- **실시간 로그 스트리밍** (SSE)
  - `/api/stream`: 테스트 실행 중 체크별 결과 실시간 전송
  - 대시보드 "Run Live" 버튼 + EventSource 7개 이벤트 타입
  - 케이스 상세 페이지에서도 Live 실행 지원

- **체크박스 케이스 선택**
  - 카테고리별 체크박스 (Normal, Fault, Verify, Config, Auto-Generated)
  - 그룹별 "all" 일괄 선택, Select All / Clear
  - "Run Selected" → `/api/run-selected` 선택된 케이스만 실행
  - 케이스별 마지막 결과 색상 dot 표시

- **Auto Rotate 모드**
  - Auto Single: 한 케이스 반복
  - Auto Rotate: 모든 케이스 순회 + 태그 필터 지원
  - 순회 중 중단 가능 (running=False 시 즉시 break)

- **대시보드 리디자인**
  - 다크 테마 기본 + 라이트 모드 토글
  - gradient 헤더, 5열 grid 통계, alert-bar
  - SVG 미니 추이 차트, 2열 레이아웃

- **Docker Compose**
  - dashboard(웹 UI) + runner(정기 실행) 2 서비스
  - reports 볼륨 공유, 환경변수 설정 (.env)

### 안정성 개선

- SSH 재시도: ssh.py (연결 레벨) + engine.py (체크 레벨)
- 지수 백오프 (2^attempt, max 10초)
- smart setup/teardown: 설정 일치 시 재부팅 skip
- 컬러 터미널 출력 (ANSI, Windows 자동 감지)
- 체크별 실행 시간 측정 (duration_ms)

### 테스트

- 119 → 205 테스트 (+86)
- CI: GitHub Actions (Python 3.9/3.11/3.12)
- 독립 검증 에이전트 11회 실행

## v1.0.0 (이전)

- SSH 기반 외부 관찰자 패턴
- 41개 수동 YAML 테스트 케이스
- 8개 체크 모듈 (process, cam_state, legacy_files, thermal, jq_forks, logs, recording, custom_commands)
- 119 테스트
- `--learn` 베이스라인 학습
- `--json` 리포트
