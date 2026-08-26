# pim-check v2.0.0

iMX8MP 타겟 QA 자동화 도구. SSH 기반 외부 관찰자 패턴으로 타겟 상태를 수집, 판정, 리포트합니다.

## 설치

```bash
# pip (권장)
pip install -e .

# 또는 의존성만
pip install pyyaml paramiko

# Docker
docker build -t pim-check .
docker run -p 8080:8080 pim-check
```

## 요구 사항

**호스트 (개발 PC):**
- Python 3.9+
- PyYAML (`pip install pyyaml`)
- **paramiko** (`pip install paramiko` — **사실상 필수**): persistent SSH client. 미설치 시 sshpass 폴백으로 자동 전환되지만 매 SSH 호출이 새 TCP/SSH handshake 가 되어 성능 저하 + target sshd 의 systemd-logind SSH session 무한 누적 (실측: comprehensive plan 25분 진행 중 한 target 콘솔에 200+ session 등록). `pim_web_viewer.py` 는 startup 시 paramiko 부재를 감지해 stderr 에 안내 출력 (PR #49 회귀 가드).
- sshpass (Linux, paramiko 폴백용): paramiko 가 설치된 환경에서는 사용되지 않음.

**타겟:**
- SSH 접속 가능
- jq, journalctl (preflight check로 자동 확인)

## 빠른 시작

```bash
# 기본 설정 생성 (~/.pim-check.yaml)
python3 pim_check.py --init-config

# 기본 헬스체크
python3 pim_check.py --host 192.168.0.5

# 특정 케이스 + HTML 리포트 + 히스토리
python3 pim_check.py --case 720p_2ch --html --history

# 대시보드 확인
python3 pim_check.py --history-report

# Plan 실행 (case 묶음 + 합격선 + 리포트 한번에)
scripts/with_pim_board.sh --for 30m --purpose "manual smoke" -- \
  python3 pim_check.py --plan smoke --host 192.168.0.5
python3 pim_check.py --list-plans
```

## CLI 플래그 전체

### 실행

| 플래그 | 설명 |
|--------|------|
| `--case NAME` | 특정 케이스 실행 |
| `--all` | 모든 케이스 순차 실행 |
| `--list` | 사용 가능한 케이스 목록 출력 |
| `--host IP` | 타겟 IP |
| `--user USER` | SSH 유저 (기본: root) |
| `--password PW` | SSH 비밀번호 (기본: root) |
| `--duration SEC` | 모니터링 시간 override (초) |
| `--include-generated` | 자동 생성 케이스 포함 |
| `--tag TAG` | 태그 필터 (예: smoke, stress) |
| `--parallel` | 다수 타겟 병렬 실행 |
| `--targets H1,H2` | 병렬 타겟 목록 (쉼표 구분) |
| `--watch SEC` | 연속 모니터링 (초 간격, Ctrl+C 종료) |
| `--quiet` | 출력 최소화 (exit code만) |

### 리포트

| 플래그 | 설명 |
|--------|------|
| `--json` | JSON 파일 저장 |
| `--html` | HTML 파일 저장 |
| `--history` | 히스토리 JSONL에 누적 |
| `--history-report` | 히스토리 대시보드 HTML 생성 |
| `--export-csv` | 히스토리를 CSV로 내보내기 |
| `--compare` | 최근 두 실행 결과 비교 |

### 생성/검증

| 플래그 | 설명 |
|--------|------|
| `--generate` | 스키마 기반 테스트 케이스 자동 생성 |
| `--validate-schema` | schema.yaml 유효성 검증 |
| `--learn` | 타겟 현재 상태 기반 베이스라인 생성 |
| `--dry-run` | 재부팅 없이 설정 차이 미리보기 |
| `--diff-targets H1,H2` | 두 타겟 간 edgeconf 비교 |

### Plan (Declarative Release)

| 플래그 | 설명 |
|--------|------|
| `--plan NAME` | profiles/plans/{name}.yaml 실행 (case 묶음 + 합격선 + 리포트) |
| `--list-plans` | 사용 가능한 plan 목록 |
| `--promote-baseline PLAN` | plan 결과를 baseline으로 promote (다음 릴리스에서 회귀 비교용) |
| `--baseline-source PATH` | promote 대상 결과 JSON 경로 (생략 시 가장 최근) |
| `--baseline-label LABEL` | baseline 라벨 (예: `v1_2`, 생략 시 source 파일명) |

### 알림/설정

| 플래그 | 설명 |
|--------|------|
| `--webhook URL` | FAIL 시 Slack/Discord webhook 알림 |
| `--log` | 실행 로그를 파일에 저장 |
| `--init-config` | ~/.pim-check.yaml 기본 설정 생성 |
| `--version` | 버전 출력 |

## 실시간 진행 모니터 & 웹 제어판

실행 중인 plan 의 진행 상황을 실시간으로 보고, 브라우저에서 타겟·플랜을 골라
테스트를 시작/중지할 수 있다(이벤트 스트림 `events/current.jsonl` 기반).

```bash
python3 pim_web_viewer.py          # http://localhost:8077 (제어판 + 실시간 뷰)
python3 pim_viewer.py              # 터미널(TUI) 뷰어
```

상세 사용법(2-pane 구조, 제어판 시작/중지, 경과 시계, 드릴다운 측정 vs 기대,
monitor_until_pass, 엔드포인트, 트러블슈팅): **[docs/realtime-monitor-guide.md](docs/realtime-monitor-guide.md)**.

> 아래 "웹 대시보드"(`web.py`, 8080)는 별개의 구버전 도구다.

## 웹 대시보드

```bash
python3 web.py                          # http://localhost:8080
python3 web.py --port 9090              # 커스텀 포트
python3 web.py --host 0.0.0.0           # 외부 접근 허용
python3 web.py --auth admin:secret      # Basic Auth
```

기능:
- 케이스 수동/자동 실행 (Run Now / Auto Start)
- 태그별 일괄 실행 (Run Smoke / Camera / Stress)
- 케이스 상세 페이지 (체크별 결과 + SVG 추이 차트)
- 다크모드 토글
- 30초 자동 새로고침

### 실시간 로그 스트리밍

"Run Live" 버튼을 클릭하면 SSE(Server-Sent Events)로 체크별 결과가 실시간 표시됩니다.

이벤트 타입: `start`, `phase`, `check_start`, `check_result`, `warning`, `error`, `done`

```javascript
// 프로그래밍 방식으로 SSE 수신
const es = new EventSource('/api/stream?case=720p_2ch&host=192.168.0.5');
es.addEventListener('check_result', e => {
  const data = JSON.parse(e.data);
  console.log(data.check, data.passed ? 'PASS' : 'FAIL', data.duration_ms + 'ms');
});
es.addEventListener('done', e => { es.close(); });
```

### API 엔드포인트

| 경로 | 설명 |
|------|------|
| `GET /` | 대시보드 |
| `GET /case/{name}` | 케이스 상세 페이지 |
| `GET /api/run?case=X&host=Y` | 테스트 실행 |
| `GET /api/run-tag?tag=smoke&host=Y` | 태그별 일괄 실행 |
| `GET /api/auto/start?case=X&interval=300` | 자동 실행 시작 |
| `GET /api/auto/stop` | 자동 실행 중지 |
| `GET /api/status` | 현재 상태 |
| `GET /api/history` | 히스토리 JSON |
| `GET /api/cases` | 케이스 목록 |
| `GET /api/case-detail?case=X` | 케이스 이력 |
| `GET /metrics` | Prometheus 메트릭 |

## 케이스 종류 (41 수동 + 20 자동)

| 분류 | 수량 | 설명 |
|------|------|------|
| 정상 모드 | 4 | 720p_2ch, 720p_4ch, fhd_4ch, rtsp_off |
| Fault 시뮬레이션 | 18 | SD, 카메라, 프로세스, RTC, 시스템, 네트워크, I2C |
| 동작 검증 | 8 | 해상도, 채널, Flip, SD, ETH0 |
| Config 체크 | 6 | Board, edgeconf, ORD/VCM, 무결성 |
| Board 체크 | 5 | HW, 에러 감지 |
| **자동 생성** | **20** | 해상도x채널xFPS(8) + hflip(4) + capture(4) + ord_vcm(4) |

## 케이스 자동 생성

`profiles/schema.yaml`에 설정 축을 정의하면 조합별 YAML 케이스가 자동 생성됩니다.

```bash
python3 pim_check.py --validate-schema   # 스키마 검증
python3 pim_check.py --generate          # 케이스 생성
```

현재 7개 축: resolution, channels, fps, hflip, capture (edgeconf) + ord_disk, vcm_srt (ord_vcm)

## 케이스 추가

`profiles/cases/`에 YAML 파일 추가. `base.yaml`을 상속하고 변경할 값만 override:

```yaml
name: "My Test"
description: "Custom test case"
tags: [smoke, camera]              # 태그 필터용
depends_on: [720p_2ch]             # 의존성 순서

setup:
  edgeconf_changes:
    ".VHL_CAM.cam_width": 1920
  reboot_after: true
  stabilize_sec: 40

checks:
  cpu:
    gst_range: [50, 95]
  custom_commands:
    - name: "My check"
      command: "echo OK"
      expected: "OK"
      on_fail: "Check failed"
```

## 플러그인 체크 모듈

`checks/plugins/`에 `BaseCheck`를 상속한 .py 파일을 넣으면 자동으로 로드됩니다.

```python
# checks/plugins/uptime_check.py
from checks.base_check import BaseCheck

class UptimeCheck(BaseCheck):
    name = "uptime"
    def collect(self, ssh, config):
        return {"uptime": ssh.run("uptime")}
    def validate(self, data, config):
        return True, "OK"
```

## 체크 모듈

| 모듈 | 체크 내용 |
|------|----------|
| process | 프로세스 존재 + CPU 사용률 |
| cam_state | /tmp/cam_state 상태 |
| legacy_files | 파일 존재/부재 확인 |
| thermal | SoC 온도 |
| jq_forks | jq 프로세스 수 |
| logs | journalctl 에러 패턴 |
| recording | 녹화 진행 상태 |
| custom_commands | YAML 정의 SSH 명령 |

## Known Issues

`profiles/base.yaml`에 알려진 이슈를 등록하면 FAIL 대신 WARN으로 표시됩니다.

```yaml
known_issues:
  - check: thermal
    reason_contains: "Temperature"
    label: "HW cooling issue (ISSUES.md #4)"

retry_policy:          # 체크별 SSH 재시도 횟수
  process: 2
  thermal: 0
  recording: 1
```

## 병렬 실행 + 타겟 프로파일

```yaml
# profiles/targets.yaml
targets:
  - host: 192.168.0.5
    user: root
    password: root
    overrides:           # 타겟별 체크 기준 override
      thermal:
        max_temp_c: 95
```

## 사용자 설정

`~/.pim-check.yaml`에 기본값을 저장하면 매번 CLI 입력이 불필요합니다.

```bash
python3 pim_check.py --init-config
```

```yaml
default_host: 192.168.0.5
default_user: root
default_password: root
webhook_url: https://hooks.slack.com/...
log_enabled: false
email:
  smtp_host: smtp.gmail.com
  smtp_port: 587
  sender: qa@example.com
  password: app-password
  recipients: [team@example.com]
```

## 환경 변수

다음 환경 변수로 코드/설정 수정 없이 동작 변경 가능:

| 변수 | 우선순위 | 설명 | 예시 |
|------|---------|------|------|
| `TARGET_HOST` | 최상위 | 타겟 IP — `run_*.py` runner와 `qa_agent`/`infer_agent`의 기본 호스트 | `scripts/with_pim_board.sh --for 30m --purpose "manual mixed_combo" -- env TARGET_HOST=192.168.0.50 python3 run_mixed_combo_verify.py` |

**우선순위 (높음→낮음)**: env var → CLI 인자(`--host`) → `~/.pim-check.yaml`(`default_host`) → `profiles/base.yaml`(`target.host`) → 코드 fallback (`192.168.0.5`).

CI(GitHub Actions hw-verify*)는 `env: TARGET_HOST: ...` 으로 워크플로우 상단에서 한 곳만 수정하면 모든 step에 자동 적용됨.

## 테스트

```bash
python3 -m pytest tests/ -v    # 256 tests, 93% coverage
```

## Plans (Declarative Release Workflow)

`profiles/plans/{name}.yaml` 한 파일에 (a) 어떤 case 묶음을 (b) 어떤 합격선으로 (c) 어떤 baseline과 비교하여 (d) 어떤 형식으로 보고할지 모두 표현한다. 매 릴리스마다 새 Python runner 스크립트를 작성하지 않아도 된다.

현재 plan (정확한 목록·케이스 수는 `--list-plans` 가 authoritative):

| Plan | 용도 | monitor_until_pass |
|------|------|:--:|
| `smoke` | 빠른 회귀 보호 (PR/build 직후 sanity) | ✅ |
| `comprehensive` | 채널 × 해상도 × 설정 종합 검증 (multi-channel, gate) | — |
| `channel_verify` | vflip/hflip/ae 토글 (720p+fhd) | ✅ |
| `nightly` | 야간 전수 회귀 | — |
| `release_next` | 다음 릴리스 게이트 (시나리오별 교체) | — |
| `rerun_failed` / `rerun_priority` | 실패 케이스 재실행 (디버그) | ✅ |
| `fault_injection` | 의도적 장애 주입 + 자동 감지/회복 검증 | — |
| `bps_quick` / `mixed_combo` | script-wrapper (전용 러너 직접 호출) | — |

> `monitor_until_pass` = 전 체크 통과 스냅샷에서 monitor 조기 종료(빠른 sanity). gate 플랜은
> 후반 drift 관측을 위해 미적용. 실시간 뷰어/제어판: [docs/realtime-monitor-guide.md](docs/realtime-monitor-guide.md).

### 운영 워크플로우

```bash
# 1. plan 실행 → reports/{plan}/{ts}.json/html/junit/md 생성
scripts/with_pim_board.sh --for 3h --purpose "manual comprehensive" -- \
  python3 pim_check.py --plan comprehensive --host 192.168.0.5

# 2. 결과 검토 후 baseline으로 promote
python3 pim_check.py --promote-baseline comprehensive --baseline-label v1_2
# → reports/comprehensive/baselines/v1_2.json 생성

# 3. plan YAML의 gate.baseline_ref.file을 새 경로로 갱신
#    file: reports/comprehensive/baselines/v1_2.json

# 4. 다음 릴리스에서 plan 다시 실행 → 자동 회귀 비교
scripts/with_pim_board.sh --for 3h --purpose "manual comprehensive" -- \
  python3 pim_check.py --plan comprehensive --host 192.168.0.5
# → regressions / fixed / new_cases 분석 + verdict (PASS/FAIL/WARN)
```

### Exit Code 매핑

| Code | 의미 |
|------|------|
| 0 | PASS (모든 case 통과 + warning 없음) |
| 1 | FAIL (regressions 또는 threshold 미달) |
| 2 | WARN (통과지만 known_issue 발생) |
| 3 | plan lint 실패 / 파일 없음 / 실행 자체 에러 |

### Plan YAML 구조 (요약)

```yaml
name: "..."
description: "..."
version: 1
cases:
  regression: [case_a, case_b, "glob_*"]   # 항상 도는 회귀 보호
  delta:      [feature_*]                  # 이번 릴리스 특화 (regression과 차집합)
execution:
  stop_on_fail: false
  case_retry: 2                            # 실패 시 재시도
  reboot_wait_sec: 300
gate:
  threshold_pass_rate: 1.0
  allow_known_issue: true
  baseline_ref:
    file: reports/{plan}/baselines/v_prev.json
    fail_on_new_failure: true              # 이전 PASS → 이번 FAIL만 차단
    new_case_policy: warn                  # baseline에 없는 신규 case
reports:
  - { format: html, path: "reports/{plan_name}/{timestamp}.html" }
  - { format: junit, path: "reports/{plan_name}/{timestamp}.xml" }
  - { format: json, path: "reports/{plan_name}/{timestamp}.json" }
  - { format: markdown_summary, path: "reports/{plan_name}/{timestamp}.md" }
```

자세한 plan 작성 가이드 + 흔한 lint 에러: `profiles/plans/AGENTS.md`.

### Migration from `run_*.py` runners

기존 6개 누적 러너는 plan으로 통합되었다. 마이그레이션 매핑:

| 기존 러너 | 대응 plan | 비고 |
|-----------|-----------|------|
| `run_comprehensive_verify.py` (368줄) | `--plan comprehensive` | 96 → 18 cases (multi-channel 단축, 4x 시간 절감). 동등성 검증: `scripts/equivalence_check.py + comprehensive_mapping.json`. |
| `run_smart_verify.py` (668줄) | (v1.2 예정) | 9 combos × A/B 패턴은 case schema 확장 필요. 현 multi case가 부분 coverage. |
| `run_mixed_combo_verify.py` (315줄) | `--plan mixed_combo` | 부분 마이그레이션 — full mixed-combo는 case schema 확장으로 v1.2. |
| `run_channel_verify.py` (160줄) | `--plan channel_verify` | 32개 1:1 매핑 (vflip + ae 토글). |
| `run_bps_quick.py` (173줄) | `--plan bps_quick` | killcam 방식 → reboot 방식 (시간 trade-off, 동일 검증). |
| `run_failed_retry.py` (139줄) | `--retry-failed` (v1.1 예정) | retry는 plan보다 CLI 플래그로 표현이 자연스러움. |

**마이그레이션 검증 워크플로우**:

```bash
# 1. 기존 러너 결과 (이미 있는 데이터)
ls comprehensive_results.json mixed_combo_results.json ...

# 2. 매핑 JSON 생성
python3 scripts/generate_comprehensive_mapping.py

# 3. 새 plan 실행
scripts/with_pim_board.sh --for 3h --purpose "manual comprehensive" -- \
  python3 pim_check.py --plan comprehensive --host 192.168.0.5

# 4. 동등성 비교
python3 scripts/equivalence_check.py \
  --left comprehensive_results.json \
  --right reports/comprehensive/{ts}.json \
  --mapping profiles/plans/comprehensive_mapping.json
# MISMATCHED 0이면 동등 — 기존 러너를 안전하게 제거 가능
```

**기존 러너 코드는 당분간 남아있음** (v1 출시 시점). v1.1에서 동등성 확정 후 deprecation 결정. CI(`hw-verify.yml`, `hw-verify-comprehensive.yml`)는 기존 러너 그대로 사용 — `hw-verify-plan.yml`이 plan-driven 대안 추가.

## 라이선스

Internal use only.
