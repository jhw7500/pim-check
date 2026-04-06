@echo off
REM pim-check Windows Setup (CMD)
echo === pim-check Windows Setup ===

REM Docker 확인
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Docker not found. Install Docker Desktop first.
    exit /b 1
)

echo [OK] Docker found

REM .env 생성
if not exist ".env" (
    copy ".env.example" ".env"
    echo [OK] .env created
    echo Edit .env to set TARGET_HOST
) else (
    echo [OK] .env exists
)

REM 빌드
echo Building...
docker-compose build
if %errorlevel% neq 0 (
    echo ERROR: Build failed
    exit /b 1
)

echo.
echo === Setup Complete ===
echo Run: docker-compose up -d
echo Open: http://localhost:8080
