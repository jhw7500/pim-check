# pim-check

iMX8MP 타겟 QA 자동화 도구. SSH 기반 외부 관찰자 패턴으로 타겟 상태를 수집, 판정, 리포트합니다.

## 요구 사항

**호스트 (개발 PC):**
- Python 3.9+
- PyYAML (`pip install pyyaml`)
- sshpass

**타겟:**
- SSH 접속 가능
- jq, journalctl (preflight check로 자동 확인)

## 사용법

```bash
# 기본 헬스체크
python3 pim_check.py

# 특정 케이스 실행
python3 pim_check.py --case 720p_2ch

# 전체 순차 실행
python3 pim_check.py --all

# 케이스 목록
python3 pim_check.py --list

# 타겟 IP/계정 지정
python3 pim_check.py --host 192.168.0.10 --user root --password root

# 모니터링 시간 지정
python3 pim_check.py --case 720p_2ch --duration 60

# JSON 리포트 저장
python3 pim_check.py --case 720p_2ch --json

# HTML 리포트 저장
python3 pim_check.py --case 720p_2ch --html

# 결과를 히스토리에 누적
python3 pim_check.py --case 720p_2ch --history

# 히스토리 대시보드 생성
python3 pim_check.py --history-report

# 현재 타겟 상태 기반 베이스라인 자동 생성
python3 pim_check.py --learn

# 스키마 기반 테스트 케이스 자동 생성
python3 pim_check.py --generate

# 자동 생성된 케이스 포함하여 전체 실행
python3 pim_check.py --all --include-generated

# 조합: 전체 실행 + HTML + 히스토리
python3 pim_check.py --all --include-generated --html --history
```

## 케이스 종류

### 정상 모드 (설정 변경 + 재부팅 + 검증)
| 케이스 | 설명 |
|--------|------|
| `720p_2ch` | 720p 2채널 |
| `720p_4ch` | 720p 4채널 |
| `fhd_4ch` | FHD 4채널 고부하 |
| `rtsp_off` | RTSP 비활성화 |

### Fault 시뮬레이션 (현재 상태 체크)
SD/저장소, 카메라, 프로세스, RTC/시간, 시스템, 네트워크, I2C 등 18개 fault 케이스

### 동작 검증 (설정 변경 + 재부팅 + 동작 확인)
해상도 변경, 채널 전환, Cam Flip, SD 미삽입 동작, ETH0 IP 변경 등 8개 verify 케이스

### Board/Config 체크 (읽기 전용)
Board 하드웨어, edgeconf 카메라/네트워크, ORD/VCM, 설정 무결성 등 6개 config 케이스

### 자동 생성 케이스
`--generate`로 `profiles/schema.yaml` 기반 자동 생성. 해상도x채널xFPS 조합 (edgeconf) + ORD/VCM 설정 조합.
수동 케이스와 중복되는 조합은 자동으로 건너뜀.

## 케이스 추가

`profiles/cases/`에 YAML 파일 추가. `base.yaml`을 상속하고 변경할 값만 override:

```yaml
name: "My Test"
description: "Custom test case"

setup:                              # 선택: 설정 변경 + 재부팅
  edgeconf_changes:
    ".VHL_CAM.cam_width": 1920
  reboot_after: true
  stabilize_sec: 40

checks:                             # 체크 항목 override
  cpu:
    gst_range: [50, 95]
  custom_commands:                  # YAML 정의 SSH 명령
    - name: "My check"
      command: "echo OK"
      expected: "OK"
      on_fail: "Check failed"
```

## 체크 모듈

| 모듈 | 체크 내용 |
|------|----------|
| process | 프로세스 존재 + CPU 사용률 |
| cam_state | /tmp/cam_state 상태 |
| legacy_files | 파일 존재/부재 확인 |
| thermal | SoC 온도 |
| jq_fork | jq 프로세스 수 |
| log | journalctl 에러 패턴 |
| recording | 녹화 진행 상태 |
| custom | YAML 정의 SSH 명령 |

## 테스트

```bash
python3 -m pytest tests/ -v
```

## 라이선스

Internal use only.
