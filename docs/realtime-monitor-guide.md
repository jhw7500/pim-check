# 실시간 모니터 & 웹 제어판 사용 가이드

pim-check 의 실시간 테스트 진행 관측(이벤트 스트림 뷰어)과 웹에서 테스트를
시작/중지하는 제어판 사용법을 다룬다. CLI 플래그 전반은 [README.md](../README.md) 참고.

> 참고: 이 문서의 뷰어(`pim_viewer` / `pim_web_viewer`)는 README 의 구버전
> 대시보드(`web.py`, 포트 8080)와 **다른 도구**다. 이쪽은 실행 중인 plan 의
> 이벤트 스트림(`events/current.jsonl`)을 실시간으로 보여준다.

---

## 1. 구조 (2-pane)

```
  pim_check.py  ──(append)──>  events/current.jsonl  <──(tail/read)──  뷰어
   (Producer)                   (이벤트 스트림, JSONL)        (pim_viewer / pim_web_viewer)
```

- **Producer** (`pim_check.py --plan ...`): 테스트를 실행하며 진행 상황을
  `events/<timestamp>_<plan>_<host>.jsonl` 에 한 줄씩(append) 기록하고,
  `events/current.jsonl` 심링크를 최신 런으로 가리킨다.
- **뷰어**: `current.jsonl` 을 읽기 전용으로 접어(fold) 현재 상태를 보여준다.
  Producer 와 독립적이라 여러 뷰어(웹/TUI)가 같은 스트림을 동시에 볼 수 있다.

이벤트 타입: `run_start`, `case_start`, `case_end`, `fail`, `pending`,
`heartbeat`, `run_end`.

---

## 2. 빠른 시작

### 2-A. CLI 로 실행 + 웹으로 관측

```bash
# 터미널 1 — 테스트 실행 (Producer)
python3 pim_check.py --plan smoke --host 192.168.214.4 --user root --password root

# 터미널 2 — 웹 뷰어 (관측)
python3 pim_web_viewer.py            # http://localhost:8077
```

### 2-B. 웹 제어판에서 직접 시작 (CLI 불필요)

```bash
python3 pim_web_viewer.py            # http://localhost:8077
```

브라우저에서 상단 제어판에 **플랜 선택 + 타겟 IP/유저/비번 입력 → ▶ 시작**.
별도로 `pim_check.py` 를 실행할 필요가 없다(뷰어가 대신 spawn).

---

## 3. 웹 제어판 (시작/중지)

상단 제어판 구성:

| 요소 | 설명 |
|------|------|
| 플랜 드롭다운 | `profiles/plans/*.yaml` 자동 목록 |
| 타겟 IP | 예: `192.168.214.4` |
| 유저 / 비번 | 기본 `root` / `root` 프리필 |
| ▶ 시작 | 검증 후 `pim_check.py` 를 백그라운드로 실행 |
| ■ 중지 | 실행 중인 런 종료(SIGTERM→SIGKILL) |

동작 규칙:

- **단일 런만 허용**: 이미 실행 중(뷰어가 시작했거나 CLI 로 시작한 라이브 런이
  있으면)이면 시작이 거부된다(`current.jsonl` 공유 충돌 방지).
- 런 종료는 자동 감지되어 ■중지 버튼이 비활성화된다.
- spawn 된 런은 항상 프로젝트의 `events/` 에 기록되고 `current.jsonl` 을 repoint
  하므로, 같은 호스트의 다른 뷰어에도 진행이 함께 보인다.

### 보안 주의

제어판은 **HTTP 로 보드에 root SSH 런을 트리거**한다. 기본 바인드는 `0.0.0.0`
이라 같은 네트워크의 누구나 호출할 수 있다. 입력은 다음과 같이 방어된다:

- 플랜은 `--list-plans` 화이트리스트에 있는 것만 허용
- 호스트/유저는 엄격한 정규식으로 제한
- 실행은 shell 없이 argv 리스트로(명령 주입 불가)

**신뢰된 네트워크에서만** 사용하고, 외부 노출이 필요하면 방화벽/리버스 프록시
인증을 앞단에 두는 것을 권장한다. 로컬 전용이면 `--host 127.0.0.1` 로 바인드.

---

## 4. 뷰어 화면 읽는 법

### 경과 시계 — 두 가지 범위

| 표시 | 의미 |
|------|------|
| 상단 **ELAPSED** | 런 전체 경과 (`run_start` 부터, 모든 케이스 + reboot 포함) |
| 케이스 배너/상세의 **경과** | 현재 케이스 내 경과 = (상단값 − 그 케이스 시작 시점) |

둘은 같은 시계에서 나오며 케이스 경과는 항상 상단보다 작다. (런이 바뀌면 시계는
새 런 기준으로 리셋된다.)

### 진행/결과

- 진행률 바 + `완료 N / 전체 M`
- **PASS / FAIL** 누적 카운트
- **FAULT 분류**:
  - `✗ FAILED (최종)` — case 결과로 확정된 실패
  - `⚠ FAULT (진행 중)` — 아직 진행 중인 fail 신호
  - 회복(resolved) — 일시 fail 후 통과로 회복된 것
- **⏳ 준비 중 (pending)** — `NEED_2_FINALIZES` 등 "아직 장애 아님, 조건 충족 대기"
  상태. fault 로 집계되지 않는다.

### 케이스 드릴다운 (케이스 클릭)

- 케이스 **설명** + **검증 N/M 통과**
- **검증 항목 체크리스트** — 항목별 상태 아이콘(✓ pass / ✗ fail / ⏳ running / ○ pending)
  - 케이스가 끝나면 항목마다 **`측정 {실측값}`** 이 표시된다(클릭 시 명령 + `측정값` /
    `기대값` 펼침). 예: `ch3 bps: 측정 8050 / 기대 ≥ 8000`, `ROT: 측정 0x000x02 / 기대 0x000x02`.
  - 실패 항목의 측정값은 빨간색.
- 중복 fault 는 `×N` 로 묶여 표시.

---

## 5. TUI 뷰어 (터미널)

```bash
python3 pim_viewer.py                 # events/current.jsonl tail
python3 pim_viewer.py --once          # 현재 스냅샷 1회 출력 후 종료
python3 pim_viewer.py <path.jsonl>    # 특정 스트림 보기
```

웹 뷰어와 동일한 `ViewerState` 를 공유한다(같은 이벤트 → 같은 상태).

---

## 6. monitor_until_pass (finalize-aware 조기 종료)

일부 플랜은 `execution.monitor_until_pass: true` 로, **모든 체크가 통과한 스냅샷이
나오는 즉시 모니터를 종료**한다. 카메라 케이스가 "부팅 후 finalize 2개"를 갖추면
통과하는데, 고정 모니터 시간을 끝까지 기다리지 않고 준비되는 즉시 끝내 단축한다.
모든 체크가 실제로 통과해야 종료하므로 검증을 약화시키지 않는다.

| 적용(빠른 sanity) | 미적용(지속검증 gate — 후반 drift 관측) |
|---|---|
| smoke, channel_verify, rerun_failed, rerun_priority | comprehensive, nightly, release_next, fault_injection |

> script-wrapper 플랜(`bps_quick`, `mixed_combo`)은 hw-verify workflow 가 plan name 으로
> 분기해 전용 러너(`run_bps_quick.py` / `run_mixed_combo_verify.py`)를 직접 호출한다.
> plan engine 의 monitor 루프를 타지 않으므로 `monitor_until_pass` 설정과 무관하다.

---

## 7. 플랜 목록

`python3 pim_check.py --list-plans` 로 항상 최신 목록 확인. 현재 plan:

| Plan | 용도 |
|------|------|
| `smoke` | 빠른 회귀 보호 (PR/build 직후 sanity) |
| `comprehensive` | 채널 × 해상도 × 설정 종합 검증 (multi-channel gate) |
| `channel_verify` | vflip/hflip/ae 토글 검증 (720p+fhd) |
| `nightly` | 야간 전수 회귀 |
| `release_next` | 다음 릴리스 게이트 (시나리오별 교체) |
| `rerun_failed` / `rerun_priority` | 실패 케이스 재실행(디버그) |
| `fault_injection` | 의도적 장애 주입 + 자동 감지/회복 검증 |
| `bps_quick` / `mixed_combo` | script-wrapper (전용 러너 직접 호출) |

---

## 8. HTTP 엔드포인트 레퍼런스 (pim_web_viewer)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 뷰어 UI |
| GET | `/state` | 현재 상태 JSON (1초 폴링) |
| GET | `/control` | 제어 상태 + plan 목록 |
| GET | `/plans` | plan 목록 |
| POST | `/start` | `{plan, host, user, password}` → 런 시작 |
| POST | `/stop` | 관리 중인 런 종료 |

`/start` 응답: `{"ok": true, "pid": 123, "plan": "...", "host": "..."}` 또는
`{"ok": false, "error": "..."}` (검증 실패 400 / 이미 실행 중 409 / spawn 실패 500).

---

## 9. 트러블슈팅

| 증상 | 원인/조치 |
|------|----------|
| 뷰어가 "이벤트 스트림 없음" | 아직 런 시작 전. `--plan` 실행 또는 제어판에서 시작 |
| `Producer lost — 신호 끊김` | 10초 이상 스트림 미갱신(프로세스 종료/멈춤). 로그 확인 |
| 시작이 409 로 거부 | 이미 라이브 런 존재. 끝나거나 ■중지 후 재시도 |
| 시계가 이전 런 값으로 시작 | 구버전 뷰어. 최신으로 갱신(run_id 전환 시 자동 리셋) |
| 포트 충돌(Address already in use) | 다른 뷰어가 점유. `--port` 로 다른 포트 사용 |

---

관련 문서: 케이스/플랜 작성은 [profiles/plans/AGENTS.md](../profiles/plans/AGENTS.md),
전체 CLI/구버전 대시보드는 [README.md](../README.md).
