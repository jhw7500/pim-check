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
