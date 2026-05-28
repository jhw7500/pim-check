# pim-check 퀵스타트 가이드

## 1분 안에 시작하기

```bash
# 설치
git clone https://github.com/jhw7500/pim-check.git
cd pim-check
python3 -m venv venv && source venv/bin/activate
pip install pyyaml paramiko

# 기본 설정
python3 pim_check.py --init-config
# ~/.pim-check.yaml에서 default_host를 타겟 IP로 수정

# 첫 테스트
python3 pim_check.py --case 720p_2ch --html --history
```

## 대시보드

```bash
python3 web.py
# 브라우저: http://localhost:8080
```

대시보드에서 할 수 있는 것:
- **Run Now** — 드롭다운에서 케이스 선택 후 실행
- **Run Live** — 실시간 로그 스트리밍
- **Run Selected** — 체크박스로 여러 케이스 선택 후 실행
- **Auto Single/Rotate** — 자동 반복 실행
- **케이스 이름 클릭** — 상세 페이지 (체크별 결과 + 추이 차트)

## Multi-target viewer

`pim_web_viewer.py` 는 단일 host 와 multi host 진행을 모두 지원한다 (v2.1.0).
기본 포트 **8077** (`--port` 로 변경 가능).

> **플랫폼**: cross-process file lock 에 `fcntl.flock` 사용 — POSIX (Linux/macOS) 권장. Windows 는 file lock 미지원이지만 `_fcntl = None` fallback (run_stream.py) 으로 import 는 안전하며 단일 process 동작은 가능. 다중 process 동시 `/start` 안전성은 후속 PR (Windows fcntl fallback) 예정.

```bash
# CLI 원커맨드 자동 시작 (단일 host) — PR #36
# 권장: 비밀번호는 PIM_PASSWORD env 로 (argv 는 ps/proc 노출 위험)
PIM_PASSWORD=xxx python3 pim_web_viewer.py --plan comprehensive \
  --target-host 192.168.0.200 --user root --until-pass
# → 서버 기동 후 0.3s 뒤 start_run() 자동 호출
# → 브라우저: http://localhost:8077 (또는 --port 지정 값)
# → --until-pass: 모든 체크 PASS 시 viewer/runner 자동 종료 (지속 실패면 계속 retry)

# 빠른 로컬 테스트용 (--password 는 ps 에 노출되므로 권장 X)
python3 pim_web_viewer.py --plan comprehensive \
  --target-host 192.168.0.200 --user root --password xxx --until-pass

# Multi-host (web UI 에서 N개 host 선택 후 Start)
python3 pim_web_viewer.py
# → http://localhost:8077 접속 → 우측 multi-target 패널의 "targets" textarea 에
#    host 를 한 줄에 하나씩 입력 (예: 192.168.214.4\n192.168.214.5) → "Start"
# → 컬럼 자동 추가, 각 컬럼별 RUNNING/DONE 배지
```

**Web API endpoint**:

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/active` | 현재 진행 중 host 목록 (`events/active.json`) |
| GET | `/api/events?host=<slug>` | 특정 host 이벤트 스트림 |
| POST | `/start` (body: `{plan, targets:[{host,user,password},...], until_pass?}`) | N개 host 동시 spawn |
| POST | `/stop` (body: `{"host": "..."}` 또는 `{"targets": [...]}`) | 선택 host stop |

> **API 보안**: `/start` body 에 password 평문 포함. 이 API 는 **localhost 전용**으로 운영 권장 — 외부 노출 시 reverse proxy + TLS 필수. `--host 0.0.0.0` 으로 바인드할 때 특히 주의 (CLI 의 `--password` ps 노출과 동일 수준의 credential 관리 필요).

**UI 흐름**:
- 단일 host: 기존 single-view 로 표시
- 2개 이상: CSS grid 컬럼 자동 분할, host 별 RUNNING/DONE 배지
- 컬럼 클릭: legacy single-view 가 해당 host 로 전환되어 상세 확인

**이벤트 저장 구조**:
```
events/
├── active.json              # 진행 중 host 인덱스
├── current.jsonl            # legacy 단일 host 진행 시에만 갱신 (multi-target 시 by-target 만 사용)
└── by-target/
    └── <slug>/              # host 별 분리 — <slug> 는 host 의 filesystem-safe 변환 (소문자 + `.` → `-`, 그 외 비-alnum → `_`; 예: 192.168.0.200 → 192-168-0-200)
```

## Docker로 실행

```bash
cp .env.example .env
# .env에서 TARGET_HOST 수정
docker-compose up -d
```

## 주요 명령어

| 할 일 | 명령어 |
|-------|--------|
| 전체 테스트 | `python3 pim_check.py --all --include-generated --html --history` |
| smoke 테스트만 | `python3 pim_check.py --all --tag smoke --history` |
| 설정 차이 확인 | `python3 pim_check.py --dry-run --include-generated` |
| 대시보드 갱신 | `python3 pim_check.py --history-report` |
| CSV 내보내기 | `python3 pim_check.py --export-csv` |
| 결과 비교 | `python3 pim_check.py --compare` |
| 케이스 자동 생성 | `python3 pim_check.py --generate` |
| CI용 JUnit XML | `python3 pim_check.py --case 720p_2ch --junit` |

## 케이스 추가하기

`profiles/cases/`에 YAML 파일 생성:

```yaml
name: "My Test"
description: "설명"
tags: [smoke]

checks:
  custom_commands:
    - name: "내 체크"
      command: "echo OK"
      expected: "OK"
      on_fail: "실패 메시지"
```

## 플러그인 체크 모듈

`checks/plugins/`에 Python 파일 추가:

```python
from checks.base_check import BaseCheck

class MyCheck(BaseCheck):
    name = "my_check"
    def collect(self, ssh, config):
        return {"result": ssh.run("my_command")}
    def validate(self, data, config):
        return data["result"] == "OK", "OK" if data["result"] == "OK" else "Failed"
```

## 도움말

```bash
python3 pim_check.py --help
python3 pim_check.py --version
```
