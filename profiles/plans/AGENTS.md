<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-30 | Updated: 2026-04-30 -->

# profiles/plans

## Purpose
Declarative Release Plan YAML 디렉토리. 각 plan 파일은 (a) 어떤 case 묶음을 (b) 어떤 합격선으로 (c) 어떤 baseline과 비교하여 (d) 어떤 형식으로 보고할지를 한 번에 표현한다. 이로써 매 릴리스마다 새 Python runner 스크립트를 작성할 필요가 없어진다.

설계 문서: `~/.gstack/projects/jhw7500-pim-check/jhw-main-design-20260430-130751.md` (v3 APPROVED).

## Key Files

| File | Description |
|------|-------------|
| `smoke.yaml` | 빠른 회귀 보호 plan (smoke 케이스만, FW build 직후 sanity check용) |
| `comprehensive.yaml` | run_comprehensive_verify.py 마이그레이션 결과 (mixed combo + AWB + bps 통합) |
| `_template.yaml` | (선택) 새 plan 작성 시 복사용 템플릿. `_` 접두사로 lint/list_plans에서 제외 |

## Plan YAML 스키마 (v1)

```yaml
name: "Plan 이름"            # 필수, 비어있지 않은 str
description: "설명"          # 필수, str
version: 1                   # 필수, 현재 SCHEMA_VERSION=1만 지원

cases:                       # 필수, regression / delta 중 최소 하나는 비어있지 않아야 함
  regression:                # 항상 도는 회귀 보호 케이스 (선택)
    - 720p_2ch               # case name 정확 일치
    - fhd_4ch
  delta:                     # 이번 릴리스 특화 (선택, regression set과 case_name 단위 차집합)
    - hflip_*                # glob 패턴 (* ? [])
    - ord_vcm_*

# 실행 정책 (선택, default 적용)
execution:
  stop_on_fail: false        # 첫 FAIL 시 중단 여부
  case_retry: 0              # 케이스별 재시도 (0 = no retry)
  retry_wait_sec: 0          # 재시도 사이 대기
  reboot_wait_sec: 300       # 재부팅 후 대기 (초)
  wait_between_cases: 0      # 케이스 간 인터벌

# 합격선 (필수)
gate:
  threshold_pass_rate: 1.0   # 0.0~1.0 (1.0 = 모두 통과)
  allow_known_issue: true    # known_issues 매칭은 WARN으로 통과
  baseline_ref:              # (선택) 이전 릴리스 결과와 비교
    file: reports/comprehensive/baselines/v1_1.json
    fail_on_new_failure: true        # 이전 PASS → 이번 FAIL만 차단
    new_case_policy: warn            # warn | skip | fail (baseline에 없는 신규 케이스)

# 출력 (선택, 기본 빈 리스트)
reports:
  - format: html             # json | html | junit (v1)
    path: reports/{plan_name}/{timestamp}.html
  - format: junit
    path: reports/{plan_name}/{timestamp}.xml
  - format: json
    path: reports/{plan_name}/{timestamp}.json
```

## Naming Convention

- 파일명 = plan name (lowercase, underscore): `comprehensive.yaml`, `release_v1_2.yaml`
- 릴리스 전용은 버전 식별자 포함: `release_{버전}.yaml` (예: `release_v1_2.yaml`)
- 일반 회귀 plan은 의미 단어: `smoke.yaml`, `nightly.yaml`
- `_` 접두사는 템플릿/예약 파일 (예: `_template.yaml`) — lint/list_plans에서 제외

## Selector 규칙 (v1)

- **str 형식만 지원**. dict는 v1에서 reject (예: `{tag: smoke}`는 v1.1로 미루어짐).
- 와일드카드 문자(`*`, `?`, `[`)가 있으면 fnmatch glob으로 처리. 없으면 정확 일치.
- glob은 `cases/`와 `generated/` 두 디렉토리를 합쳐서 검색.
- selector가 어떤 case와도 매칭되지 않으면 `resolve_cases`가 ValueError로 명시적 실패. 오타가 silent하게 통과하지 않음.

## baseline_ref 작성 / Promote 규칙

**baseline_ref.file은 명시적 경로만 가리킨다.** `latest.json` 같은 자동 갱신 파일은 사용하지 않음 (self-reference로 false negative 발생 가능 — 동일 plan 재실행 시 baseline=직전 결과가 되어 regressions=0 silent false negative).

**Promote 절차** (수동, v1):
1. plan 실행 후 `reports/{plan_name}/{timestamp}.json` 생성됨
2. 결과를 검토하여 baseline으로 승격할지 결정
3. `cp reports/{plan_name}/{timestamp}.json reports/{plan_name}/baselines/v{version}.json`
4. 다음 plan 실행 시 `gate.baseline_ref.file`이 위 baseline 파일을 가리키게 갱신

baseline 파일이 mtime 30일 초과면 lint/runtime에서 WARN 출력 (실행 자체는 진행).

## 흔한 lint 에러 사례

| 에러 메시지 키워드 | 원인 | 해결 |
|---|---|---|
| `필수 키 누락: 'X'` | name/description/version/cases/gate 중 누락 | 누락 키 추가 |
| `version` + `99` | SCHEMA_VERSION(1)과 다른 값 | `version: 1` |
| `gate.mode + deprecated` | v3에서 제거된 키 사용 | `gate.mode` 삭제, `threshold_pass_rate`로 대체 |
| `tag + v1.1` | `{tag: smoke}` 사용 | name 또는 glob으로 대체 (v1.1까지 대기) |
| `dict selector` | `{foo: bar}` 같은 dict | str로 변경 |
| `cases.baseline` (unknown section) | `cases.baseline:` 사용 (예전 명명) | `cases.regression:`으로 변경 |
| `비어있지 않은` | regression+delta 모두 빈 리스트 | 최소 한 섹션은 항목 추가 |
| `threshold_pass_rate + 범위` | 0.0~1.0 밖 값 | 0.0~1.0 사이 |
| `format 미지원` | json/html/junit 외 값 | 셋 중 하나 |
| `new_case_policy` | warn/skip/fail 외 값 | 셋 중 하나 |

## For AI Agents

### Working In This Directory
- 새 plan 추가 시 `_template.yaml`(있으면) 복사 후 수정. 없으면 위 스키마를 참조해 작성.
- plan 추가 후 `python pim_check.py --plan {name}` 실행하지 말고 먼저 lint를 확인:
  ```python
  from plan import load_plan
  load_plan("profiles/plans/{name}.yaml")  # ValueError 발생 시 에러 메시지 검토
  ```
- `comprehensive.yaml`은 `run_comprehensive_verify.py`의 1:1 마이그레이션 결과 — 임의로 case 추가/제거하지 말고 동등성 검증(`scripts/equivalence_check.py`)을 통과한 상태로 유지.

### Testing Requirements
- `tests/test_plan_load.py` — load_plan/lint_plan 단위 테스트
- `tests/test_plan_resolve.py` — resolve_cases 단위 테스트
- 새 plan 추가 후 위 두 테스트 + 기존 256 tests 모두 통과 확인 (`pytest`)

### Common Patterns
- regression 섹션은 "릴리스와 무관하게 항상 도는 안전망"
- delta 섹션은 "이번 릴리스 변경점에 닿는 케이스"
- 두 섹션은 case_name 단위 차집합 — 중복 실행은 자동 회피 (regression 우선)
- 결과 dict의 `_section` 키로 어느 섹션에서 실행되었는지 라벨링됨 (v1.1+)

## Dependencies

### Internal
- `plan.py` — 로딩/lint/resolution 로직
- `config.py` — base.yaml 머지 (execute_plan에서 사용, v1.1+)
- `engine.py` — 실제 case 실행 (v1.1+)
- `cases/`, `generated/` — selector 매칭 대상

<!-- MANUAL: -->
