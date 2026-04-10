<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-09 | Updated: 2026-04-09 -->

# deploy

## Purpose
프로덕션 배포 설정 파일. Grafana 대시보드와 systemd 서비스 정의를 포함한다.

## Key Files

| File | Description |
|------|-------------|
| `grafana-dashboard.json` | Grafana 대시보드 JSON 모델 (pim-check 결과 시각화) |
| `pim-check-web.service` | systemd 유닛 파일 — 웹 대시보드를 Linux 서비스로 실행 |

## For AI Agents

### Working In This Directory
- Grafana JSON 수정 시 대시보드 ID/UID 변경에 주의.
- systemd 서비스 파일 수정 후 `systemctl daemon-reload` 필요.

<!-- MANUAL: -->
