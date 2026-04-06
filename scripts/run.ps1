# pim-check Run Single Test (PowerShell)
# 사용법: .\scripts\run.ps1 720p_2ch
#         .\scripts\run.ps1 720p_2ch 192.168.0.5

param(
    [Parameter(Mandatory=$true)]
    [string]$Case,

    [string]$TargetHost = "192.168.0.5"
)

Write-Host "Running case: $Case on $TargetHost" -ForegroundColor Cyan

docker-compose run --rm dashboard python pim_check.py --host $TargetHost --case $Case --html --history --junit

if ($LASTEXITCODE -eq 0) {
    Write-Host "PASS" -ForegroundColor Green
} else {
    Write-Host "FAIL" -ForegroundColor Red
}
