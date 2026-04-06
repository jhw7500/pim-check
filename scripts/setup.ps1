# pim-check Windows Setup Script (PowerShell)
# 사용법: .\scripts\setup.ps1

Write-Host "=== pim-check Windows Setup ===" -ForegroundColor Cyan

# Docker 확인
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Docker not found. Install Docker Desktop first." -ForegroundColor Red
    Write-Host "  https://docs.docker.com/desktop/install/windows-install/"
    exit 1
}

# Docker 실행 확인
docker info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker is not running. Start Docker Desktop first." -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Docker found" -ForegroundColor Green

# .env 생성
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[OK] .env created from .env.example" -ForegroundColor Green
    Write-Host ""
    Write-Host "Edit .env to set your target IP:" -ForegroundColor Yellow
    Write-Host "  TARGET_HOST=192.168.0.5"
    Write-Host ""
} else {
    Write-Host "[OK] .env already exists" -ForegroundColor Green
}

# 빌드
Write-Host "Building Docker image..." -ForegroundColor Cyan
docker-compose build
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Build complete" -ForegroundColor Green
} else {
    Write-Host "ERROR: Build failed" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Edit .env (set TARGET_HOST)"
Write-Host "  2. Run: docker-compose up -d"
Write-Host "  3. Open: http://localhost:8080"
Write-Host ""
Write-Host "Or use quick scripts:"
Write-Host "  .\scripts\start.ps1    Start dashboard + runner"
Write-Host "  .\scripts\stop.ps1     Stop all services"
Write-Host "  .\scripts\run.ps1      Run a single test"
