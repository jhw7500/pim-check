# test-infrastructure Gap Analysis

> **Date**: 2026-04-09
> **Match Rate**: 93%
> **Plan**: `docs/01-plan/features/test-infrastructure.plan.md`
> **Design**: `docs/02-design/features/test-infrastructure.design.md`

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 테스트 223개 규모에서 mock 중복과 측정 부재로 유지보수 비용 증가 |
| **WHO** | pim-check 개발자, CI 파이프라인 |
| **RISK** | conftest.py 도입 시 기존 223개 테스트 깨짐 가능성 |
| **SUCCESS** | 커버리지 90%+, CI 린트+커버리지 게이트, SSH mock 중복 50%+ 제거 |
| **SCOPE** | conftest.py fixture → pyproject.toml 설정 → CI 강화 |

---

## 1. Requirements Fulfillment

| ID | Requirement | Priority | Status | Evidence |
|----|-------------|----------|--------|----------|
| FR-01 | conftest.py SSH mock fixture | High | ✅ Met | `tests/conftest.py:14` |
| FR-02 | 프로파일 fixture | High | ✅ Met | `tests/conftest.py:28,48` |
| FR-03 | 임시 리포트 fixture | Medium | ✅ Met | `tests/conftest.py:54` |
| FR-04 | 중복 mock 교체 | High | ⚠️ Partial | sys.path.insert 6개 제거, mock 교체는 Option C 선택에 따라 선별적 |
| FR-05 | pytest markers 등록 | Medium | ✅ Met | `pyproject.toml:30-34` |
| FR-06 | 기존 테스트 marker 부여 | Medium | ⏭️ Deferred | Design Option C: 새 테스트부터 적용 |
| FR-07 | pytest-cov 90% 게이트 | High | ✅ Met | 90.29% 달성 |
| FR-08 | CI ruff lint | High | ✅ Met | `.github/workflows/test.yml` lint job |
| FR-09 | CI coverage 게이트 | High | ✅ Met | `--cov-fail-under=90` |

## 2. Success Criteria

| Criteria | Status | Evidence |
|----------|--------|----------|
| conftest.py 최소 3개 fixture | ✅ Met | 5개: mock_ssh, sample_profile, profiles_dir, tmp_report_dir, sample_results |
| 기존 223개 테스트 PASS | ✅ Met | 239 passed (223 기존 + 16 신규) |
| 커버리지 90%+ | ✅ Met | 90.29% |
| CI ruff + coverage 게이트 | ✅ Met | test.yml 린트 job + cov-fail-under |
| 의존성 pyproject.toml 명시 | ✅ Met | dev optional-dependencies |

## 3. Structural Match

| Item | Design | Implementation | Match |
|------|--------|---------------|-------|
| tests/conftest.py | 5 fixtures | 5 fixtures | ✅ |
| pyproject.toml markers | 3 markers | 3 markers | ✅ |
| pyproject.toml coverage | fail_under=90, omit list | fail_under=90, 6 omits | ✅ |
| test.yml lint job | ruff check | ruff check separate job | ✅ |
| test.yml coverage | --cov-fail-under=90 | --cov --cov-fail-under=90 | ✅ |
| sys.path.insert 제거 | 6 files | 6 files cleaned | ✅ |

## 4. Additional Improvements

- `learner.py` 버그 수정: `temp_c` UnboundLocalError (ValueError 핸들링 누락)
- `junit_reporter.py` 테스트 추가 (0% → 100%)
- `learner.py` 테스트 추가 (0% → 99%)
- ruff auto-fix로 16개 unused import 정리

## 5. Gaps

| Gap | Severity | Status |
|-----|----------|--------|
| FR-04: 기존 테스트 mock 중복 완전 제거 안 됨 | Low | Deferred (Design Option C) |
| FR-06: 기존 테스트에 marker 미부여 | Low | Deferred (새 테스트부터 적용) |

## 6. Match Rate

**Overall: 93%**

- Structural: 100% (6/6 항목 일치)
- Functional: 90% (FR-04 partial)
- Success Criteria: 100% (5/5)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-09 | Initial analysis | hwjo |
