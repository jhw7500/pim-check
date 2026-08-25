<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-09 | Updated: 2026-04-30 -->

# scripts

## Purpose
운영·검증 목적의 스크립트가 공존한다. Windows 환경 자동화, Plan 운영,
PIM 보드 점유, 개발 워크플로 도구를 각각의 실행 환경에 맞게 제공한다.

## Key Files

### Windows 운영 (PowerShell/Batch)

| File | Description |
|------|-------------|
| `setup.ps1` | PowerShell: Python venv 생성 + 의존성 설치 (paramiko 포함) |
| `setup.bat` | Batch: setup.ps1 호출 래퍼 |
| `start.ps1` | PowerShell: 웹 대시보드 서버 시작 |
| `start.bat` | Batch: start.ps1 호출 래퍼 |
| `stop.ps1` | PowerShell: 실행 중인 서버 종료 |
| `stop.bat` | Batch: stop.ps1 호출 래퍼 |
| `run.ps1` | PowerShell: CLI 테스트 실행 래퍼 |

### Plan 운영 도구 (Python)

| File | Description |
|------|-------------|
| `equivalence_check.py` | run_*.py 결과 vs plan-driven 결과 동등성 비교 (binary). MATCHED / MISMATCHED / LEFT_ONLY / RIGHT_ONLY 카테고리 분류. `--mapping` JSON 옵션으로 도메인 case_name 매핑 지원. |
| `generate_comprehensive_mapping.py` | run_comprehensive_verify의 96 scenario를 8 mandatory multi case로 자동 매핑 JSON 생성. `profiles/plans/comprehensive_mapping.json` 출력. |

### PIM 보드 점유 도구 (Shell/Python)

| File | Description |
|------|-------------|
| `with_pim_board.sh` | 공통 exclusive lease 래퍼. mode-600 control 환경을 로드하고 사용자 로컬 `jhw-control` 절대 경로로 자식 하드웨어 명령을 실행한다. |
| `run_with_deadline.py` | long-lease automation 자식을 lease 종료 전 TERM→teardown→KILL 순서로 종료하고 프로세스 그룹을 회수한다. |
| `guard_pim_board_command.py` | Claude Bash PreToolUse 가드. `env` option cluster·`builtin`·`exec`·`eval`·`source`·`nice`·`stdbuf`·`xargs`·`setsid`·`sudo` 등 launcher·shell `-c`·stdin shell fail-closed·명령 치환·그룹/조건 제어문 내부의 직접 plan/standalone runner 실행도 차단한다. 정확한 저장소 wrapper 경로만 면제하며, 비정규 경로와 축약 불가능한 복합 문법은 fail-closed 처리한다. 방어 심화용이며 보안 경계는 아니다. |
| `test_vflip_frame_compare.sh` | edgeconf 변경·재부팅·녹화를 수행하는 standalone vflip 비교 러너. 직접 또는 shell positional script로 실행할 때도 lease가 필요하다. |
| `auto_chain.sh` | `smoke → comprehensive → release_next → nightly` 자동 체인. 실행 상태 생성 전 24시간 long lease로 자신을 래핑한다. |
| `auto_overnight.sh` | 다음 09:00 KST까지 자동 체인. 정확한 deadline까지 long lease로 자신을 래핑한다. |
| `auto_weekend.sh` | 월요일 09:00 KST까지 자동 체인. 정확한 deadline까지 long lease로 자신을 래핑한다. |

### 개발 워크플로 도구 (Python)

| File | Description |
|------|-------------|
| `pr_reviews.py` | PR 자동리뷰 3종(Claude·Gemini·Codex) 집계 + 머지 게이트. 세 리뷰어가 서로 다른 API 경로에 남기므로 한 경로만 보면 리뷰를 통째로 놓친다. 봇 신원과 automation 마커 일치를 검증하고, Claude/Gemini의 무지적 선언은 인용·코드·HTML 주석이 아닌 독립 상태 줄에서만 인정한다. issue 코멘트와 Codex review→인라인 코멘트를 두 라운드 조회해 목록 안정성·review id 참조를 확인하고 마지막 PR HEAD를 freshness 권위로 쓰며, clear/finding 어느 쪽도 확인되지 않거나 스냅샷이 변하면 INCOMPLETE로 차단한다. 처분은 신뢰 구성원의 코멘트에 독립 줄 `<!-- pr-review-disposition -->`과 영숫자 근거가 있는 `Decision: <근거>`(또는 `판단:`/`처분 근거:`) 줄이 있을 때만 인정하며, PR 메인 처분은 전체 finding·NON_CLEAR에, 인라인 답글은 `in_reply_to_id`가 가리키는 finding 1건에만 적용한다. `--full`은 Codex parent와 선택된 인라인 finding 원문을 모두 출력한다. `--gate`는 MISSING/FAILED/STALE/INCOMPLETE/NON_CLEAR/미처분 FINDINGS에서 exit 1. |

## For AI Agents

### Working In This Directory
- **Windows 스크립트** (.ps1/.bat): `.bat` 파일은 `.ps1`의 단순 래퍼 — 로직 수정은 `.ps1`에서. 줄바꿈은 CRLF 유지. 실행 권한(+x) 확인 불필요.
- **Python 도구**: shebang `#!/usr/bin/env python3` + 실행 권한(+x). `from __future__ import annotations` 사용. 단위 테스트는 `tests/test_{module}.py`.
- 두 카테고리는 다른 OS 환경 — 하나 수정해도 다른 쪽 영향 없음.

### PIM 보드 lease 사용

실제 plan 또는 standalone hardware runner는 반드시 공통 래퍼로 실행한다.

```bash
scripts/with_pim_board.sh --for 30m --purpose "manual smoke" -- \
  python3 pim_check.py --plan smoke
```

| 실행 주체/범위 | Lease | 비고 |
|---|---|---|
| CI mixed-combo | 30m | `hw-verify.yml` |
| CI comprehensive | 3h | `hw-verify-comprehensive.yml` |
| CI plan | 12h | `hw-verify-plan.yml` |
| 로컬 `auto_chain.sh` | 24h | `--long-lease true` 필요 |
| 로컬 overnight/weekend 자동화 | 정확한 종료 deadline (`--until`) | `--long-lease true` 필요 |

long lease는 만료 31분 전에 정리를 시작한다. 자식 프로세스에는 teardown용 30분을
주고, 마지막 60초는 강제 종료와 lease 해제에 남긴다.

보드가 busy이면 exit 4로 즉시 실패한다. `board wait`, 재시도 루프, `|| true`, 직접
`jhw-control board acquire`는 사용하지 않는다. lease는 advisory이며 #108은 persistent
web dashboard, Docker runner, non-plan CLI 모드를 소급 적용하지 않는다.

### Plan 도구 사용 패턴

```bash
# 1. 매핑 JSON 생성 (1회 또는 schema 변경 시)
python3 scripts/generate_comprehensive_mapping.py
# → profiles/plans/comprehensive_mapping.json

# 2. 동등성 비교 (마이그레이션 검증)
python3 scripts/equivalence_check.py \
  --left comprehensive_results.json \
  --right reports/comprehensive/{ts}.json \
  --mapping profiles/plans/comprehensive_mapping.json
```

### PR 리뷰 게이트 사용 패턴

```bash
# 머지 직전 — 세 리뷰어 상태를 한 번에 확인
python3 scripts/pr_reviews.py 103

# 게이트로 사용 (위반 시 exit 1)
python3 scripts/pr_reviews.py 103 --gate || echo "머지 보류"

# 리뷰 본문 전체 읽기 / 기계 판독
python3 scripts/pr_reviews.py 103 --full
python3 scripts/pr_reviews.py 103 --json
```

STALE(리뷰 대상 커밋 != HEAD)은 Codex에서 상시 발생한다 — Codex는 PR open 시
한 번만 리뷰하고 이후 push에는 재리뷰하지 않는다. PR에 `@codex review`를
코멘트하면 재리뷰가 트리거된다.

### Testing
- Python 도구: `tests/test_equivalence_check.py`, `tests/test_generate_mapping.py`, `tests/test_pr_reviews.py`
- 세 테스트 모두 sys.path manipulation으로 scripts/ import (test 파일 헤더 참조)

<!-- MANUAL: -->
