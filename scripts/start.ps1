# pim-check Start (PowerShell)
# 사용법: .\scripts\start.ps1

Write-Host "Starting pim-check..." -ForegroundColor Cyan
docker-compose up -d

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "pim-check is running!" -ForegroundColor Green
    Write-Host "  Dashboard: http://localhost:$((Get-Content .env | Select-String 'DASHBOARD_PORT=(\d+)' | ForEach-Object { $_.Matches.Groups[1].Value }) ?? '8080')"
    Write-Host "  Logs:      docker-compose logs -f"
    Write-Host "  Stop:      .\scripts\stop.ps1"
    Write-Host ""

    # 브라우저 자동 열기
    Start-Process "http://localhost:8080"
} else {
    Write-Host "ERROR: Failed to start" -ForegroundColor Red
}
