<!-- Generated: 2026-04-09 | Updated: 2026-04-09 -->

# pim-check

## Purpose
iMX8MP 타겟 보드 QA 자동화 도구 (v2.0.0). SSH로 타겟에 접속하여 프로세스, 카메라 상태, 온도, 녹화, 로그 등을 체크하고 PASS/FAIL 리포트를 생성한다. YAML 프로파일 기반 테스트 케이스 시스템으로 설정 변경 → 재부팅 → 검증 → 복원 사이클을 자동화한다.

## Key Files

| File | Description |
|------|-------------|
| `pim_check.py` | CLI 엔트리포인트. argparse 기반 명령행 인터페이스 (`--case`, `--all`, `--parallel`, `--watch`, `--plan`, `--list-plans`, `--promote-baseline` 등) |
| `engine.py` | QA 체크 엔진. 스냅샷 1회 실행(`run_snapshot`) 및 모니터 루프(`run_monitor`) + thermal shutdown 복구 |
| `plan.py` | Declarative Release Plan layer — load/lint/resolve/execute/gate/render. case 묶음 + 합격선 + reporting을 plan YAML 1개에 표현. 6개 누적 run_*.py 통합 wedge. |
| `ssh.py` | SSH 클라이언트 래퍼. paramiko persistent client (필수 의존성) + sshpass 호환 폴백. `SshClient`, `SshTimeoutError`, `SshConnectionError` |
| `config.py` | YAML 설정 로더. `base.yaml` + `cases/{name}.yaml` 딥 머지 패턴 |
| `setup.py` | `SetupManager` — edgeconf JSON 변경, 백업/복원, 재부팅 대기 |
| `reporter.py` | 텍스트/JSON 결과 리포터 |
| `html_reporter.py` | HTML 리포트 생성기 |
| `junit_reporter.py` | JUnit XML 리포트 (CI 연동) |
| `history.py` | JSONL 히스토리 저장 + CSV 내보내기 + HTML 대시보드 생성 |
| `compare.py` | 최근 두 실행 결과 비교 |
| `parallel.py` | `ThreadPoolExecutor` 기반 다중 타겟 병렬 실행 |
| `web.py` | 내장 HTTP 서버 웹 대시보드 (테스트 실행, 자동 실행, 결과 조회 API) |
| `generator.py` | `schema.yaml` 기반 테스트 케이스 자동 생성 (축 조합 × 기대값 규칙) |
| `learner.py` | 타겟 현재 상태를 베이스라인으로 학습 |
| `notifier.py` | FAIL 시 webhook 알림 |
| `notifier_email.py` | FAIL 시 이메일 알림 |
| `user_config.py` | `~/.pim-check.yaml` 사용자 설정 로더 |
| `logger.py` | 실행 로그 파일 출력 (`FileLogger`) |
| `color.py` | 터미널 ANSI 컬러 출력 유틸 |
| `stream.py` | 스트리밍 유틸리티 |
| `runner_loop.py` | Docker 컨테이너용 정기 실행 루프 |
| `Dockerfile` | Python 3.11-slim 기반 컨테이너 이미지 |
| `docker-compose.yml` | dashboard + runner 2-서비스 구성 |
| `pyproject.toml` | 빌드 설정. Python >=3.9, 의존성: pyyaml, paramiko |
| `requirements.txt` | pip 의존성 목록 |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `checks/` | QA 체크 모듈 (BaseCheck 서브클래스들) — `checks/AGENTS.md` 참조 |
| `profiles/` | YAML 테스트 프로파일 (base + cases + generated + schema + plans) — `profiles/AGENTS.md` 참조 |
| `tests/` | pytest 테스트 스위트 — `tests/AGENTS.md` 참조 |
| `scripts/` | Windows 실행/설정 스크립트 — `scripts/AGENTS.md` 참조 |
| `deploy/` | 배포 설정 (Grafana, systemd) — `deploy/AGENTS.md` 참조 |
| `reports/` | 테스트 결과 출력 디렉토리 (gitignore 대상, 런타임 생성) |
| `docs/` | 참조 문서 (체크리스트 Excel 등) |
| `.github/workflows/` | CI/CD GitHub Actions |

## For AI Agents

### Working In This Directory
- 모든 체크 로직은 `checks/` 디렉토리의 `BaseCheck` 서브클래스로 구현. 루트의 `engine.py`는 체크를 실행만 한다.
- `config.py`의 `deep_merge(base, override)` 패턴이 핵심 — base.yaml 값을 case yaml이 부분 오버라이드.
- SSH 명령은 반드시 `SshClient.run()`을 통해 실행. 직접 subprocess 호출 금지.
- `setup.py`의 `SetupManager`는 edgeconf 변경 시 반드시 backup → apply → reboot → test → restore → reboot 순서를 지킨다.
- `reporter.py`, `html_reporter.py`, `junit_reporter.py`는 결과 포맷만 담당. 체크 로직을 넣지 않는다.

### Testing Requirements
- `pytest` 실행: 프로젝트 루트에서 `pytest`
- SSH 의존 테스트는 모킹 처리됨 — 실제 타겟 불필요
- 새 체크 추가 시 `tests/test_checks_{name}.py` 테스트 필수

### Architecture Overview
```
CLI (pim_check.py)
  ├── config.py (YAML 로드 + 딥 머지)
  ├── ssh.py (타겟 SSH 연결)
  ├── setup.py (edgeconf 변경/복원/재부팅)
  ├── engine.py (체크 실행 루프)
  │     └── checks/ (8개 체크 + 플러그인)
  ├── reporter.py / html / junit (결과 출력)
  ├── history.py (히스토리 JSONL)
  ├── parallel.py (다중 타겟)
  └── web.py (HTTP 대시보드)
```

### Common Patterns
- 결과 dict 구조: `{"name": str, "passed": bool, "reason": str, "data": dict, "duration_ms": int}`
- 프로파일 dict 구조: `{"target": {...}, "monitor": {...}, "checks": {...}, "setup": {...}, "known_issues": [...]}`
- known_issue가 매칭된 FAIL은 `"known_issue"` 키가 추가되어 WARN으로 표시됨

## Dependencies

### External
- `pyyaml` — YAML 파싱 (필수)
- `paramiko` — SSH 클라이언트 (필수, 전 platform). persistent transport 재사용으로 DUT sshd 부하 감소. 부수 패키지(`cryptography`, `bcrypt`, `cffi`, `pynacl`) ~10MB 설치 증가 trade-off — 매 호출 새 connect 의 누적 부담을 제거하는 게 우선.

<!-- MANUAL: -->
