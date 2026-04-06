# pim-check Stop (PowerShell)
Write-Host "Stopping pim-check..." -ForegroundColor Cyan
docker-compose down
Write-Host "Stopped." -ForegroundColor Green
