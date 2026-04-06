# pim-check Windows 설치 가이드

## 사전 요구사항

- **Docker Desktop** ([다운로드](https://docs.docker.com/desktop/install/windows-install/))
- Docker Desktop 설정에서 WSL 2 백엔드 활성화 (권장)

## 원클릭 설치

### PowerShell (권장)

```powershell
git clone https://github.com/jhw7500/pim-check.git
cd pim-check
.\scripts\setup.ps1
```

### CMD

```cmd
git clone https://github.com/jhw7500/pim-check.git
cd pim-check
scripts\setup.bat
```

## 실행

### 대시보드 + 자동 실행

```powershell
# 시작 (브라우저 자동 열림)
.\scripts\start.ps1

# 중지
.\scripts\stop.ps1
```

### 단일 테스트 실행

```powershell
.\scripts\run.ps1 720p_2ch
.\scripts\run.ps1 720p_2ch 192.168.214.4
```

### docker-compose 직접 사용

```powershell
# .env 설정
copy .env.example .env
notepad .env    # TARGET_HOST 수정

# 실행
docker-compose up -d

# 대시보드: http://localhost:8080
# 로그: docker-compose logs -f runner
# 중지: docker-compose down
```

## 네트워크 설정

Docker Desktop에서 타겟 보드(192.168.x.x)에 접근하려면:

### 같은 네트워크인 경우 (유선/WiFi)

대부분 자동으로 동작합니다. Docker Desktop이 호스트 네트워크를 공유합니다.

### 접근 안 되는 경우

1. **Docker Desktop 설정 확인**
   - Settings > Resources > Network
   - "Enable host networking" 활성화

2. **방화벽 확인**
   ```powershell
   # PowerShell에서 타겟 접근 테스트
   Test-NetConnection -ComputerName 192.168.0.5 -Port 22
   ```

3. **WSL2 네트워크 모드** (Docker Desktop + WSL2 사용 시)
   
   `%UserProfile%\.wslconfig` 파일 생성/수정:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   WSL 재시작: `wsl --shutdown` 후 Docker Desktop 재시작

## 볼륨 경로

Windows에서 Docker 볼륨은 자동 관리됩니다:
- reports 볼륨: `docker volume inspect pim-check_reports`
- profiles는 프로젝트 디렉토리에서 읽기 전용 마운트

## 트러블슈팅

| 증상 | 해결 |
|------|------|
| `docker-compose` 명령 없음 | Docker Desktop 최신 버전 설치 또는 `docker compose` (하이픈 없이) 사용 |
| 빌드 실패 | Docker Desktop 실행 중인지 확인. Settings > Docker Engine 확인 |
| 타겟 접속 안 됨 | 위 네트워크 설정 참고. `Test-NetConnection` 으로 확인 |
| 포트 8080 충돌 | `.env`에서 `DASHBOARD_PORT=9090` 으로 변경 |
| 한글 깨짐 | PowerShell에서 `chcp 65001` 실행 (UTF-8 설정) |
