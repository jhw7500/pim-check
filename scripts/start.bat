@echo off
echo Starting pim-check...
docker-compose up -d
if %errorlevel% equ 0 (
    echo pim-check is running!
    echo Dashboard: http://localhost:8080
    start http://localhost:8080
) else (
    echo ERROR: Failed to start
)
