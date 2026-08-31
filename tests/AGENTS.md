<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-09 | Updated: 2026-04-09 -->

# tests

## Purpose
pytest 테스트 스위트. 모든 핵심 모듈에 대한 단위 테스트와 통합 테스트를 포함한다. SSH 의존 코드는 mock 처리하여 실제 타겟 없이 실행 가능.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | 테스트 패키지 초기화 |
| `test_cli.py` | CLI argparse 및 main() 흐름 테스트 |
| `test_engine.py` | Engine 스냅샷/모니터/머지 테스트 |
| `test_config.py` | deep_merge, load_profile 테스트 |
| `test_ssh.py` | SshClient subprocess 폴백 테스트 |
| `test_ssh_paramiko.py` | SshClient paramiko 모드 테스트 |
| `test_setup.py` | SetupManager 설정 변경/복원/재부팅 테스트 |
| `test_reporter.py` | Reporter 포맷/JSON 출력 테스트 |
| `test_html_reporter.py` | HTML 리포트 생성 테스트 |
| `test_history.py` | JSONL 히스토리/대시보드/CSV 테스트 |
| `test_compare.py` | 실행 결과 비교 테스트 |
| `test_parallel.py` | 다중 타겟 병렬 실행 테스트 |
| `test_generator.py` | 스키마 기반 케이스 생성 테스트 |
| `test_stream.py` | 스트리밍 유틸 테스트 |
| `test_web.py` | 웹 대시보드 API 테스트 |
| `test_checks_*.py` | 개별 체크 모듈 테스트 (process, cam_state, thermal, recording, log, custom, jq_fork, legacy) |
| `test_plugins.py` | 플러그인 동적 로드 테스트 |
| `test_notifier.py` | Webhook 알림 테스트 |
| `test_notifier_email.py` | 이메일 알림 테스트 |
| `test_user_config.py` | 사용자 설정 로드 테스트 |
| `test_logger.py` | 파일 로거 테스트 |
| `test_integration.py` | 엔드투엔드 통합 테스트 |
| `test_simulation.py` | 장애 시뮬레이션 테스트 |
| `test_gaps.py` | 테스트 커버리지 갭 확인 |
| `test_hw_gate_rules.py` / `test_hw_gate_evidence.py` | canonical numeric rule, schema, verdict precedence/coverage fail-closed 검증 |
| `test_hw_gate_baseline.py` / `test_hw_gate_calibration.py` | committed baseline binding과 human-review-only three-run candidate 검증 |
| `test_hw_gate_transaction.py` / `test_hw_gate_cli.py` | strict snapshot/journal recovery, HEAD-bound artifacts, terminal finalization 검증 |
| `test_hw_gate_adapter_*.py` / `test_checks_*_evidence.py` | BPS·mixed-combo adapter와 scoped evidence collector의 mocked SSH 검증 |
| `test_hw_gate_publisher.py` / `test_integration_hw_evidence_workflows.py` | split workflow trust binding, marker upsert, stale HEAD, permissions/lease contract 검증 |
| `fixtures/hw_gate/` | schema-valid evidence, baseline, raw collector golden fixtures |

## For AI Agents

### Working In This Directory
- 실행: 프로젝트 루트에서 `pytest` (pyproject.toml에 testpaths 설정됨)
- SSH mock 패턴: `SshClient` 인스턴스의 `run` 메서드를 lambda나 MagicMock으로 교체
- 새 체크 추가 시 `test_checks_{name}.py` 파일 생성 필수 — collect/validate 각각 테스트
- hardware evidence tests are local-only: mock SSH, GitHub REST, signal/transaction seams.
  Do not acquire a PIM lease, run a workflow, or require a physical target. Controlled
  hardware acceptance is a separately authorized phase.
- baseline candidate tests must preserve the review boundary: calibration writes a separate
  candidate and never mutates `baselines/hw-baseline.json`; missing/unbaselined metrics must
  remain ERROR rather than becoming PASS.
- 파일명 규칙 (pim-check#94 로 3분류 보완 — 코퍼스 실태 반영):
  - **모듈 대응**: `test_{module}.py` 또는 `test_{module}_{topic}.py` — 접두가
    대상 모듈과 1:1 (예: `test_plan_gate`, `test_setup_snapshot`,
    `test_checks_cam_state_heartbeat`). 주제가 크면 모듈 파일에 합치지 말고
    `_{topic}` 분리를 택한다 — docstring 의 "무엇을 왜 못박는가" 문맥이
    회귀의 기전·재현 데이터를 담는 자리다.
  - **코퍼스 가드**: `test_cases_{topic}.py` — 특정 모듈이 아니라 profiles/ 의
    케이스·셸 명령 성질을 못박는 파일 (예: `test_cases_kernel_log_source`).
  - **교차 경로(통합) 가드**: `test_integration_{topic}.py` — 여러 실행 경로를
    함께 보는 파일 (예: `test_integration_teardown_recovery`).

### Testing Requirements
- `pytest` 단독 실행으로 전체 통과 확인
- 린트: `ruff check`

## Dependencies

### Internal
- 프로젝트 루트의 모든 Python 모듈을 import하여 테스트

### External
- `pytest` — 테스트 프레임워크

<!-- MANUAL: -->
