<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-09 | Updated: 2026-04-30 -->

# scripts

## Purpose
두 가지 목적의 스크립트가 공존:
1. **Windows 운영 스크립트** — PowerShell/Batch로 가상환경 설정, 서버 시작/종료 자동화
2. **Plan 운영 도구** (Python) — equivalence_check, comprehensive 매핑 생성기

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

### 개발 워크플로 도구 (Python)

| File | Description |
|------|-------------|
| `pr_reviews.py` | PR 자동리뷰 3종(Claude·Gemini·Codex) 집계 + 머지 게이트. 세 리뷰어가 서로 다른 API 경로에 남기므로 한 경로만 보면 리뷰를 통째로 놓친다. 봇 신원과 automation 마커 일치를 검증하고, Claude/Gemini의 무지적 선언은 인용·코드·HTML 주석이 아닌 독립 상태 줄에서만 인정한다. issue 코멘트와 Codex review→인라인 코멘트를 두 라운드 조회해 목록 안정성·review id 참조를 확인하고 마지막 PR HEAD를 freshness 권위로 쓰며, clear/finding 어느 쪽도 확인되지 않거나 스냅샷이 변하면 INCOMPLETE로 차단한다. 처분은 신뢰 구성원의 코멘트에 독립 줄 `<!-- pr-review-disposition -->`과 영숫자 근거가 있는 `Decision: <근거>`(또는 `판단:`/`처분 근거:`) 줄이 있을 때만 인정하며, PR 메인 처분은 전체 finding·NON_CLEAR에, 인라인 답글은 `in_reply_to_id`가 가리키는 finding 1건에만 적용한다. `--full`은 Codex parent와 선택된 인라인 finding 원문을 모두 출력한다. `--gate`는 MISSING/FAILED/STALE/INCOMPLETE/NON_CLEAR/미처분 FINDINGS에서 exit 1. |

## For AI Agents

### Working In This Directory
- **Windows 스크립트** (.ps1/.bat): `.bat` 파일은 `.ps1`의 단순 래퍼 — 로직 수정은 `.ps1`에서. 줄바꿈은 CRLF 유지. 실행 권한(+x) 확인 불필요.
- **Python 도구**: shebang `#!/usr/bin/env python3` + 실행 권한(+x). `from __future__ import annotations` 사용. 단위 테스트는 `tests/test_{module}.py`.
- 두 카테고리는 다른 OS 환경 — 하나 수정해도 다른 쪽 영향 없음.

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
