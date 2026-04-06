# Changelog

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
