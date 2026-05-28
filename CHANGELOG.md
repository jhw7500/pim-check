# Changelog

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
