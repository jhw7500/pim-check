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
- sshpass (Linux) 또는 paramiko (`pip install paramiko`, Windows/Linux 모두 지원)

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

### 알림/설정

| 플래그 | 설명 |
|--------|------|
| `--webhook URL` | FAIL 시 Slack/Discord webhook 알림 |
| `--log` | 실행 로그를 파일에 저장 |
| `--init-config` | ~/.pim-check.yaml 기본 설정 생성 |
| `--version` | 버전 출력 |

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
| `TARGET_HOST` | 최상위 | 타겟 IP — `run_*.py` runner와 `qa_agent`/`infer_agent`의 기본 호스트 | `TARGET_HOST=192.168.0.50 python3 run_mixed_combo_verify.py` |

**우선순위 (높음→낮음)**: env var → CLI 인자(`--host`) → `~/.pim-check.yaml`(`default_host`) → `profiles/base.yaml`(`target.host`) → 코드 fallback (`192.168.0.5`).

CI(GitHub Actions hw-verify*)는 `env: TARGET_HOST: ...` 으로 워크플로우 상단에서 한 곳만 수정하면 모든 step에 자동 적용됨.

## 테스트

```bash
python3 -m pytest tests/ -v    # 256 tests, 93% coverage
```

## 라이선스

Internal use only.
