@echo off
echo ====================================================
echo  Outward Voyager Agent Launcher
echo ====================================================
echo.

cd /d "C:\Projects\Outward Voyager\agent"

REM Check if data dir needs seeding
if not exist "data\skills.db" (
    echo [SETUP] First run detected -- seeding databases...
    py seed_data.py
    echo.
)

echo [INFO] Starting agent (Ctrl+C to stop)...
py main.py
