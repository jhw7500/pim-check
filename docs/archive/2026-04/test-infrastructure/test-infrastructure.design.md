# test-infrastructure Design Document

> **Project**: pim-check v2.0.0
> **Author**: hwjo
> **Date**: 2026-04-09
> **Plan Reference**: `docs/01-plan/features/test-infrastructure.plan.md`

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 테스트 223개 규모에서 mock 중복(116건)과 측정 부재로 유지보수 비용 증가 |
| **WHO** | pim-check 개발자, CI 파이프라인 |
| **RISK** | conftest.py 도입 시 기존 223개 테스트 깨짐 가능성 |
| **SUCCESS** | 커버리지 90%+, CI 린트+커버리지 게이트, SSH mock 중복 50%+ 제거 |
| **SCOPE** | conftest.py fixture → pyproject.toml 설정 → CI 강화 |

---

## 1. Overview

테스트 인프라 체계화를 위한 설계. 기존 223개 테스트를 깨뜨리지 않으면서 conftest.py fixture 도입, 커버리지 측정, CI 품질 게이트를 추가한다.

**선택한 설계: Option C — Pragmatic Balance**

기존 테스트 코드를 대규모로 리팩터링하지 않고, conftest.py에 공통 fixture를 추가하여 새 테스트부터 활용하도록 한다. 기존 테스트는 가장 빈번한 중복 패턴만 선별적으로 교체한다.

---

## 2. Architecture Options (Evaluated)

| | Option A: Minimal | Option B: Full Refactor | **Option C: Pragmatic (Selected)** |
|---|---|---|---|
| conftest.py | 3개 fixture | 10+ fixture, 전체 리팩터링 | 5개 fixture, 선별적 교체 |
| 기존 테스트 변경 | 0개 | 30개 전부 | sys.path.insert 제거(6개) + 고빈도 mock 교체(~10개) |
| 리스크 | 낮음 | 높음 (전면 리팩터링) | 중간 |
| 효과 | 미미 | 최대 | 실용적 (80/20) |
| 시간 | 30분 | 3시간+ | 1시간 |

---

## 3. File Changes

### 3.1 New Files

| File | Purpose |
|------|---------|
| `tests/conftest.py` | 공통 pytest fixture 정의 |

### 3.2 Modified Files

| File | Change |
|------|--------|
| `pyproject.toml` | pytest markers, addopts(cov) 추가, dev 의존성 추가 |
| `.github/workflows/test.yml` | ruff lint + coverage gate 단계 추가 |
| `tests/test_cli.py` | sys.path.insert 제거 |
| `tests/test_ssh.py` | sys.path.insert 제거 |
| `tests/test_gaps.py` | sys.path.insert 제거 |
| `tests/test_config.py` | sys.path.insert 제거 |
| `tests/test_setup.py` | sys.path.insert 제거 |
| `tests/test_reporter.py` | sys.path.insert 제거 |

---

## 4. Detailed Design

### 4.1 conftest.py Fixtures

```python
# tests/conftest.py

@pytest.fixture
def mock_ssh():
    """기본 SSH mock — run()은 None 반환"""
    ssh = MagicMock(spec=SshClient)
    ssh.run.return_value = None
    ssh.check_connectivity.return_value = True
    ssh.preflight_check.return_value = []
    return ssh

@pytest.fixture
def sample_profile():
    """base.yaml 기반 최소 프로파일 dict"""
    return {
        "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
        "monitor": {"duration_sec": 0, "interval_sec": 5},
        "checks": {
            "processes": {"required": ["gstApp"], "optional": []},
            "cpu": {"bg_check_max_pct": 3.0, "gst_range": [0, 100]},
            "thermal": {"max_temp_c": 93, "warn_temp_c": 88},
            "cam_state": {"dir": "/tmp/cam_state", "expected_state": "healthy",
                         "valid_states": ["healthy", "degraded"], "max_streak": 0},
            "logs": {"error_patterns": ["kernel panic"]},
        },
    }

@pytest.fixture
def profiles_dir():
    """실제 profiles/ 디렉토리 절대 경로"""
    return os.path.join(os.path.dirname(__file__), "..", "profiles")

@pytest.fixture
def tmp_report_dir(tmp_path):
    """임시 리포트 디렉토리"""
    return str(tmp_path / "reports")

@pytest.fixture
def sample_results():
    """표준 체크 결과 리스트 (2 pass, 1 fail)"""
    return [
        {"name": "process", "passed": True, "reason": "OK", "data": {}, "duration_ms": 50},
        {"name": "thermal", "passed": True, "reason": "OK", "data": {"max_temp": 72.0}, "duration_ms": 30},
        {"name": "cam_state", "passed": False, "reason": "state='failed'", "data": {}, "duration_ms": 20},
    ]
```

### 4.2 pyproject.toml 변경

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "unit: 단위 테스트 (기본)",
    "integration: 통합 테스트 (여러 모듈 연동)",
    "simulation: 장애 시뮬레이션 테스트",
]
addopts = "--strict-markers"

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff>=0.4"]
```

### 4.3 CI Workflow 강화

```yaml
# test.yml에 추가할 단계
- name: Install dev dependencies
  run: pip install pyyaml pytest pytest-cov ruff

- name: Lint
  run: ruff check .

- name: Run tests with coverage
  run: python -m pytest --cov=. --cov-report=term-missing --cov-fail-under=90 -q
```

---

## 5. Implementation Guide

### 5.1 Implementation Order

1. `tests/conftest.py` 생성 (fixture 정의)
2. `pyproject.toml` 업데이트 (markers, dev deps)
3. sys.path.insert 제거 (6개 파일)
4. pytest-cov 설치 + 커버리지 측정
5. 커버리지 부족 시 테스트 보완
6. `.github/workflows/test.yml` 업데이트
7. 전체 테스트 실행 + 검증

### 5.2 Session Guide

| Module | Files | Effort |
|--------|-------|--------|
| module-1: conftest + config | conftest.py, pyproject.toml | 핵심 |
| module-2: sys.path cleanup | 6개 test 파일 | 간단 |
| module-3: coverage | pytest-cov 설치 + 측정 + 보완 | 중간 |
| module-4: CI | test.yml | 간단 |

---

## 6. Test Plan

| Test | Method | Expected |
|------|--------|----------|
| 기존 223개 테스트 통과 | `pytest -q` | 223 passed |
| conftest fixture 동작 | fixture 사용하는 새 테스트 실행 | PASS |
| 커버리지 90%+ | `pytest --cov --cov-fail-under=90` | PASS |
| ruff 린트 | `ruff check .` | 0 errors |
| CI 워크플로우 | GitHub Actions 실행 | green |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-09 | Initial design | hwjo |
