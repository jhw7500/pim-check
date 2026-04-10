<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-09 | Updated: 2026-04-09 -->

# scripts

## Purpose
Windows 환경용 PowerShell/Batch 실행 스크립트. 가상환경 설정, 서버 시작/종료를 자동화한다.

## Key Files

| File | Description |
|------|-------------|
| `setup.ps1` | PowerShell: Python venv 생성 + 의존성 설치 (paramiko 포함) |
| `setup.bat` | Batch: setup.ps1 호출 래퍼 |
| `start.ps1` | PowerShell: 웹 대시보드 서버 시작 |
| `start.bat` | Batch: start.ps1 호출 래퍼 |
| `stop.ps1` | PowerShell: 실행 중인 서버 종료 |
| `stop.bat` | Batch: stop.ps1 호출 래퍼 |
| `run.ps1` | PowerShell: CLI 테스트 실행 래퍼 |

## For AI Agents

### Working In This Directory
- `.bat` 파일은 `.ps1`의 단순 래퍼 — 로직 수정은 `.ps1`에서.
- 스크립트 수정 후 실행 권한(+x) 확인 불필요 (Windows 전용).
- 줄바꿈은 CRLF 유지.

<!-- MANUAL: -->
