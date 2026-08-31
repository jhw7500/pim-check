<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-09 | Updated: 2026-04-09 -->

# checks

## Purpose
QA 체크 모듈 디렉토리. `BaseCheck` 추상 클래스를 상속하는 13개 체크가 구현되어 있으며, `plugins/` 디렉토리를 통한 동적 플러그인 로드를 지원한다. `__init__.py`의 `ALL_CHECKS` 리스트가 등록 목록이고, normal engine은 `snapshot` scope만 실행한다.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | `ALL_CHECKS` 리스트 정의 + `load_plugins()` 자동 로드. 체크 추가 시 여기에 등록 |
| `base_check.py` | `BaseCheck` ABC — `collect(ssh, config) -> dict`, `validate(data, config) -> (bool, str)` |
| `process.py` | `ProcessCheck` — 필수/선택 프로세스 실행 여부 + CPU 사용률 (pgrep + ps) |
| `cam_state.py` | `CamStateCheck` — `/tmp/cam_state/` 상태 파일·에러 스트릭 + BG_Check 감시자 heartbeat(`heartbeat_max_age_sec`, 기본 30s) 체크 |
| `cam_health.py` | `CamHealthCheck` — gstApp 내장 camera-health v1 producer 스냅샷(`/run/pim-camera/gstApp.json`) 신선도 + FAIL observation |
| `max9296_abi.py` | `Max9296AbiCheck` — max9296 드라이버 버전(modinfo) + prepare/health_raw sysfs ABI 상태 |
| `thermal.py` | `ThermalCheck` — CPU/SoC 온도 (`/sys/devices/virtual/thermal/thermal_zone*/temp`) |
| `jq_fork.py` | `JqForkCheck` — jq 프로세스 과다 포크 감지 |
| `log.py` | `LogCheck` — journalctl 에러 로그 패턴 매칭 (kernel panic, OOM 등) |
| `recording.py` | `RecordingCheck` — gstApp 녹화 진행 상태 (journalctl progress 파싱) |
| `custom.py` | `CustomCommandCheck` — YAML 정의 커스텀 명령 실행 + 위험 명령 차단 |
| `legacy.py` | `LegacyFileCheck` — 존재하면 안 되는 레거시 파일 검사 |
| `target_identity.py` | `TargetIdentityCheck` — allowlisted module/file SHA·version identity를 `SshClient.run()`으로 수집 (`hardware_evidence` scope) |
| `bps_evidence.py` | `BpsEvidenceCheck` — current-boot finalized MP4와 `ffprobe` numeric BPS evidence를 수집; tolerance policy는 소유하지 않음 (`hardware_evidence` scope) |
| `mixed_combo_evidence.py` | `MixedComboEvidenceCheck` — i2c mode mask와 ISP register word를 numeric evidence로 수집 (`hardware_evidence` scope) |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `plugins/` | 사용자 확장 체크 플러그인. `BaseCheck` 서브클래스를 `.py` 파일로 두면 자동 로드 |

## For AI Agents

### Working In This Directory
- 새 체크 추가 절차: (1) `base_check.py`의 `BaseCheck`를 상속하는 클래스 작성 (2) `__init__.py`의 `ALL_CHECKS`에 인스턴스 추가 (3) `tests/test_checks_{name}.py` 테스트 작성
- 모든 체크는 `collect()` → `validate()` 2단계 패턴을 따른다. collect는 SSH로 데이터 수집, validate는 수집 데이터를 config 기준값과 비교.
- `collect()`에서 SSH 실패 시 예외를 던지지 않고 빈 dict나 에러 정보를 반환. 예외 처리는 `engine.py`가 담당.
- `config` 파라미터는 프로파일의 `checks` 섹션 전체가 전달됨 — 각 체크가 자기 키를 꺼내 사용.
- `custom.py`의 `_DANGEROUS_PATTERNS` — 타겟이 root 권한이므로 위험 명령 차단 필수.
- `target_identity`/`bps_evidence`/`mixed_combo_evidence`는 반드시
  `scope = "hardware_evidence"`를 유지한다. `Engine` snapshot 실행 경로에 이들을
  섞지 말고 `hw_gate` adapter만 collector 결과를 canonical evidence로 정규화한다.
- hardware collector는 `SshClient.run()`만 사용한다. direct SSH/`sshpass` subprocess,
  exit-only PASS, 또는 collector 안의 baseline threshold policy를 추가하지 않는다.

### Testing Requirements
- 각 체크별 테스트: `tests/test_checks_{name}.py`
- SSH mock 패턴: `ssh.run = lambda cmd: "mocked_output"`
- collect와 validate를 분리 테스트할 것
- hardware collector 테스트는 `tests/test_checks_target_identity.py`,
  `tests/test_checks_bps_evidence.py`, `tests/test_checks_mixed_combo_evidence.py`에
  mocked SSH로 작성한다. 실제 board lease는 unit test에서 취득하지 않는다.

### Common Patterns
```python
class MyCheck(BaseCheck):
    name = "my_check"

    def collect(self, ssh, config: dict) -> dict:
        output = ssh.run("some command")
        return {"raw": output}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if problem:
            return (False, "reason")
        return (True, "OK")
```

## Dependencies

### Internal
- `ssh.py` — `SshClient` 인스턴스가 collect()에 전달됨
- `engine.py` — `ALL_CHECKS`를 import하여 순회 실행
- `hw_gate/` — hardware-evidence scope collector의 raw output을 baseline-backed
  canonical evidence로 평가하고 strict restoration을 소유

<!-- MANUAL: -->
