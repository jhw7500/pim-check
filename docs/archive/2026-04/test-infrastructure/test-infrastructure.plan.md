# test-infrastructure Planning Document

> **Summary**: 테스트 인프라 체계화 — conftest.py fixture 통합, pytest-cov 커버리지 90%+, CI 품질 게이트, 테스트 marker 분류
>
> **Project**: pim-check
> **Version**: 2.0.0
> **Author**: hwjo
> **Date**: 2026-04-09
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 223개 테스트가 30개 파일에 분산되어 있으나, SSH mock 중복, fixture 미공유, 커버리지 측정 없음, CI에 린트/커버리지 게이트 없음 — 테스트 유지보수 비용이 증가하고 품질 하락을 감지할 수 없음 |
| **Solution** | conftest.py로 공통 fixture 통합, pytest-cov로 커버리지 90% 게이트, ruff 린트 CI 통합, pytest marker로 테스트 분류(unit/integration/simulation) |
| **Function/UX Effect** | 새 체크 추가 시 fixture 재사용으로 테스트 작성 시간 단축, CI에서 커버리지 하락/린트 오류 자동 차단 |
| **Core Value** | 테스트 인프라가 성장에 맞게 확장 가능해지고, 품질 회귀를 CI 레벨에서 자동 방어 |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 테스트 223개 규모에서 mock 중복과 측정 부재로 유지보수 비용 증가 |
| **WHO** | pim-check 개발자, CI 파이프라인 |
| **RISK** | conftest.py 도입 시 기존 223개 테스트 깨짐 가능성 |
| **SUCCESS** | 커버리지 90%+ 달성, CI에서 린트+커버리지 게이트 동작, conftest.py로 SSH mock 중복 50% 이상 제거 |
| **SCOPE** | Phase 1: conftest.py + fixture / Phase 2: pytest-cov + marker / Phase 3: CI 게이트 강화 |

---

## 1. Overview

### 1.1 Purpose

pim-check 테스트 스위트(223개, 3,284 lines)의 인프라를 체계화한다. 현재 각 테스트 파일이 독립적으로 SSH mock을 생성하고, 커버리지 측정이 없으며, CI에 린트 게이트가 없다. 이를 해결하여 테스트 유지보수성을 높이고 품질 회귀를 자동으로 방어한다.

### 1.2 Background

- 프로젝트가 v2.0.0에 도달하며 테스트 223개로 성장
- SSH mock 패턴이 거의 모든 테스트 파일에서 반복됨 (`ssh = MagicMock(); ssh.run.side_effect = ...`)
- `test_gaps.py`로 갭 보완을 시작했으나 체계적 커버리지 관리는 미비
- CI는 `pytest -v` 실행만 하고, 린트(`ruff`)나 커버리지 게이트 없음

### 1.3 Related Documents

- `AGENTS.md` — 프로젝트 아키텍처 및 테스트 요구사항
- `.github/workflows/test.yml` — 현재 CI 파이프라인
- `tests/AGENTS.md` — 테스트 디렉토리 가이드

---

## 2. Scope

### 2.1 In Scope

- [x] `tests/conftest.py` 생성 — 공통 fixture (mock SSH, mock profile, tmp report dir 등)
- [x] 기존 테스트 파일에서 중복 mock 코드를 conftest fixture로 교체
- [x] `pytest-cov` 도입 및 커버리지 90% 게이트 설정
- [x] pytest marker 도입 (`@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.simulation`)
- [x] `pyproject.toml`에 pytest 설정 확장 (markers, cov 옵션)
- [x] `.github/workflows/test.yml` 강화 — ruff lint + 커버리지 게이트 추가

### 2.2 Out of Scope

- E2E 테스트 (실제 타겟 보드 연결)
- Docker 기반 테스트
- 성능/부하 테스트
- 테스트 병렬 실행 (pytest-xdist)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `tests/conftest.py`에 공통 SSH mock fixture (`mock_ssh`) 정의 | High | Pending |
| FR-02 | `tests/conftest.py`에 프로파일 fixture (`sample_profile`, `profiles_dir`) 정의 | High | Pending |
| FR-03 | `tests/conftest.py`에 임시 리포트 디렉토리 fixture (`tmp_report_dir`) 정의 | Medium | Pending |
| FR-04 | 기존 테스트 파일에서 중복 mock 패턴을 conftest fixture로 교체 | High | Pending |
| FR-05 | `pytest.ini_options`에 markers 등록 (unit, integration, simulation) | Medium | Pending |
| FR-06 | 기존 테스트에 적절한 marker 부여 | Medium | Pending |
| FR-07 | `pytest-cov` 의존성 추가 및 커버리지 90% 게이트 설정 | High | Pending |
| FR-08 | CI에 `ruff check` 린트 단계 추가 | High | Pending |
| FR-09 | CI에 `pytest --cov --cov-fail-under=90` 커버리지 게이트 추가 | High | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 호환성 | 기존 223개 테스트 전부 PASS 유지 | `pytest` 실행 |
| 커버리지 | 라인 커버리지 90% 이상 | `pytest --cov` |
| 린트 | ruff 경고 0개 | `ruff check` |
| CI 속도 | 테스트 실행 시간 10초 이내 유지 | GitHub Actions 로그 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] `tests/conftest.py` 존재하고 최소 3개 fixture 정의
- [ ] 기존 223개 테스트 전부 PASS
- [ ] 커버리지 90% 이상 달성
- [ ] CI에서 ruff + 커버리지 게이트 동작
- [ ] 새로 추가한 의존성은 `pyproject.toml`에 명시

### 4.2 Quality Criteria

- [ ] 커버리지 90% 이상
- [ ] ruff check 경고 0개
- [ ] conftest fixture 사용으로 SSH mock 중복 코드 50% 이상 감소

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| conftest.py fixture가 기존 테스트의 mock 패턴과 충돌 | High | Medium | fixture를 opt-in 방식으로 설계 (자동 주입 아닌 명시적 파라미터) |
| 커버리지 90% 미달 | Medium | Low | 현재 223개 테스트로 이미 높은 커버리지 예상, 부족 시 핵심 모듈 보완 |
| ruff 린트 기존 코드에서 다수 경고 발생 | Medium | Medium | 기존 경고는 별도 PR로 정리, CI에서는 새 코드만 검사하거나 단계적 도입 |
| pytest-cov 의존성이 Python 3.9와 호환 문제 | Low | Low | pytest-cov는 Python 3.9 지원 확인됨 |

---

## 6. Impact Analysis

### 6.1 Changed Resources

| Resource | Type | Change Description |
|----------|------|--------------------|
| `tests/conftest.py` | New File | 공통 fixture 정의 파일 생성 |
| `tests/test_*.py` (30개) | Test Files | 중복 mock 코드를 fixture 참조로 교체 |
| `pyproject.toml` | Config | pytest markers, cov 옵션 추가 |
| `.github/workflows/test.yml` | CI | ruff + coverage 단계 추가 |
| `requirements.txt` | Dependencies | pytest-cov, ruff 추가 |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `pyproject.toml` | READ | `pip install -e .`, CI | Needs verification |
| `test.yml` | READ | GitHub Actions | Needs verification |
| `tests/test_*.py` | EXECUTE | `pytest` | Needs verification |

### 6.3 Verification

- [ ] 변경 후 `pytest` 전체 통과 확인
- [ ] `ruff check` 실행하여 린트 상태 확인
- [ ] CI 워크플로우가 올바르게 동작하는지 확인

---

## 7. Architecture Considerations

### 7.1 Project Level Selection

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| **Starter** | 단순 구조 | 소규모 프로젝트 | ☐ |
| **Dynamic** | 기능 기반 모듈 | 중규모 앱 | ☒ |
| **Enterprise** | 엄격한 레이어 분리 | 대규모 시스템 | ☐ |

### 7.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| Test Framework | pytest / unittest | pytest | 이미 사용 중, fixture 시스템 활용 |
| Coverage Tool | pytest-cov / coverage.py | pytest-cov | pytest 통합, CLI 옵션으로 게이트 설정 |
| Linter | ruff / flake8 / pylint | ruff | pyproject.toml에 이미 ruff 설정 가능, 빠른 속도 |
| Fixture 전략 | autouse / explicit | explicit | 기존 테스트 깨뜨리지 않도록 명시적 주입 |
| Marker 체계 | flat / hierarchical | flat | unit/integration/simulation 3단계로 충분 |

### 7.3 conftest.py Fixture 설계

```
tests/conftest.py
├── mock_ssh          — SshClient mock (run 메서드 기본 동작 설정)
├── mock_ssh_connected — check_connectivity=True, preflight=[] 포함
├── sample_profile    — base.yaml 기반 기본 프로파일 dict
├── profiles_dir      — 실제 profiles/ 디렉토리 경로
├── tmp_report_dir    — tmp_path 기반 임시 리포트 디렉토리
└── sample_results    — 표준 체크 결과 리스트
```

---

## 8. Convention Prerequisites

### 8.1 Existing Project Conventions

- [x] `CLAUDE.md` has coding conventions section
- [ ] `AGENTS.md` has testing requirements
- [ ] ESLint/Prettier (N/A — Python project)
- [x] `pyproject.toml` exists with pytest config

### 8.2 Conventions to Define/Verify

| Category | Current State | To Define | Priority |
|----------|---------------|-----------|:--------:|
| **Test naming** | `test_{module}.py` | 유지 — 변경 불필요 | Low |
| **Fixture naming** | 없음 | `mock_*` (mock 객체), `sample_*` (데이터), `tmp_*` (임시 경로) | High |
| **Marker 사용** | 없음 | `@pytest.mark.unit` (기본), `integration`, `simulation` | Medium |
| **Import 순서** | 비일관적 | stdlib → third-party → local (ruff I 규칙) | Medium |

---

## 9. Next Steps

1. [ ] Design 문서 작성 (`test-infrastructure.design.md`)
2. [ ] conftest.py 구현 + 기존 테스트 리팩터링
3. [ ] pyproject.toml 업데이트 (markers, cov 설정)
4. [ ] CI 워크플로우 강화
5. [ ] 커버리지 90% 달성 확인

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-09 | Initial draft | hwjo |
