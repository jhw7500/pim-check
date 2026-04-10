<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-09 | Updated: 2026-04-09 -->

# profiles

## Purpose
YAML 기반 테스트 프로파일 디렉토리. `base.yaml`이 공통 기본값을 정의하고, `cases/`의 개별 케이스가 필요한 값만 오버라이드하는 딥 머지 패턴. `schema.yaml`로 축 조합 기반 케이스 자동 생성도 지원한다.

## Key Files

| File | Description |
|------|-------------|
| `base.yaml` | 공통 기본값 — target(host/user/password), monitor(duration/interval), checks 기준값, retry_policy, known_issues |
| `schema.yaml` | 테스트 케이스 자동 생성 스키마 — sources/axes/combinations 정의 + expectations 규칙 |
| `targets.yaml` | 다중 타겟 병렬 실행용 호스트 목록 + 타겟별 overrides |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `cases/` | 수동 작성 테스트 케이스 YAML (verify_*, fault_*, config_*, board_* 등) |
| `generated/` | `--generate` 명령으로 자동 생성된 케이스 (gen_* 접두사) |

## For AI Agents

### Working In This Directory
- `base.yaml` 수정 시 모든 케이스에 영향 — 변경 전 전체 테스트 확인 필수.
- 케이스 YAML 구조: `setup.edgeconf_changes` (설정 변경), `checks` (기준값 오버라이드), `tags` (필터용), `depends_on` (의존성 정렬)
- `schema.yaml`의 `sources.{name}.axes.{axis}.combinations` 구조를 이해해야 generator 수정 가능.
- generated/ 파일은 직접 수정하지 말 것 — `--generate`로 재생성.

### Naming Convention
- 수동 케이스: `verify_*` (검증), `fault_*` (장애 시뮬레이션), `config_*` (설정 변경), `board_*` (하드웨어)
- 자동 생성: `gen_{resolution}_{channels}_{fps}.yaml` 등 schema의 filename_pattern을 따름

### Profile Merge Order
```
base.yaml → cases/{name}.yaml (deep_merge)
          → CLI overrides (--host, --duration 등)
```

## Dependencies

### Internal
- `config.py` — `load_profile()` 함수가 base + case 머지 수행
- `generator.py` — `schema.yaml` 파싱 및 generated/ 케이스 생성

<!-- MANUAL: -->
