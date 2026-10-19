@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title Solo PIXI Module Test Analysis - One-Click Launcher
color 0A

echo ==============================================================
echo   Solo PIXI Module Test Analysis - One-Click Launcher
echo ==============================================================
echo.

:: --- 1. Check Docker --------------------------------------------
echo [1/6] Checking Docker...
docker info >nul 2>&1
if %ERRORLEVEL% neq 0 (
    color 0C
    echo ERROR: Docker is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)
echo       Docker is running. OK

:: --- 2. Build and start containers ------------------------------
echo.
echo [2/6] Starting Docker containers (PostgreSQL + API)...
cd /d "%~dp0solo-pixi-essential"
docker compose up -d --build
if %ERRORLEVEL% neq 0 (
    color 0C
    echo ERROR: Docker Compose failed.
    pause
    exit /b 1
)
echo       Containers started.

:: --- 3. Docker Compose Wait ----------------------------------------
echo.
echo       PostgreSQL and API are ready. (Healthchecks passed during startup)


:: --- 5. Upload outlog test data ---------------------------------
echo.
echo [5/6] Uploading outlog test data into database...

:: Check if data already exists
for /f "tokens=*" %%a in ('docker exec pixi-test-db psql -U pixi -d pixi_test -t -c "SELECT COUNT(*) FROM module_test;" 2^>nul') do set DB_COUNT=%%a
set DB_COUNT=%DB_COUNT: =%
if "%DB_COUNT%"=="" set DB_COUNT=0

if %DB_COUNT% gtr 0 (
    echo       Database already has %DB_COUNT% records. Skipping upload.
    echo       (To re-upload, run: docker exec pixi-test-db psql -U pixi -d pixi_test -c "TRUNCATE module_test;"^)
) else (
    echo       Copying log files into API container...
    if exist "%~dp0rawlogs" (
        docker cp "%~dp0rawlogs" pixi-test-api:/app/outlog
    ) else (
        docker cp "%~dp0outlog" pixi-test-api:/app/outlog
    )
    if %ERRORLEVEL% neq 0 (
        echo WARNING: Could not copy rawlogs/outlog folder. Skipping data upload.
        goto skip_upload
    )

    echo       Parsing and inserting records...
    docker exec pixi-test-api python module_log_parser.py /app/outlog --dsn postgresql://pixi:pixipass@postgres/pixi_test
    if %ERRORLEVEL% neq 0 (
        echo WARNING: Data upload had errors. Check output above.
    ) else (
        echo       Data upload complete.
    )

    :: Clean up copied files
    docker exec pixi-test-api rm -rf /app/outlog >nul 2>&1
)
:skip_upload

:: --- 6. Open browser -------------------------------------------
echo.
echo [6/6] Opening dashboard in browser...
echo.
echo ==============================================================
echo   Dashboard URL:  http://localhost:8001
echo ==============================================================
echo.
start "" "http://localhost:8001"

echo Done! Press any key to exit (containers will keep running).
echo To stop containers:  cd solo-pixi-essential ^& docker compose down
pause >nul
