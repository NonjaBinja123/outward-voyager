@echo off
echo ====================================================
echo  Outward Voyager — Full Launch
echo ====================================================
echo.

REM ── Step 1: Close the game gracefully, then wait for Steam to settle
echo [1/5] Closing game if running...

REM Try graceful close first (lets Steam process the disconnection cleanly)
taskkill /im "Outward Definitive Edition.exe" 2>nul
timeout /t 3 /nobreak >nul

REM Force-kill anything still lingering
taskkill /f /im "Outward Definitive Edition.exe" 2>nul

REM Wait for Steam to fully process the session close.
REM Root cause of first-load failure: SteamworksManager:InitAPI() fails if Steam
REM hasn't finished disconnecting the previous session. 8s is enough.
echo    Waiting for Steam to settle...
timeout /t 8 /nobreak >nul

REM ── Step 2: Clean + Build mod
echo [2/5] Building mod (clean + build)...
"C:\Program Files\dotnet\dotnet.exe" clean "C:\Projects\Outward Voyager\mod\OutwardVoyager\OutwardVoyager.csproj" -c Release -v quiet
"C:\Program Files\dotnet\dotnet.exe" build  "C:\Projects\Outward Voyager\mod\OutwardVoyager\OutwardVoyager.csproj" -c Release -v quiet

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Build failed. Fix errors before launching.
    pause
    exit /b 1
)
echo Build succeeded.
echo.

REM ── Step 3: Launch game
echo [3/5] Launching Outward Definitive Edition...
start "" "C:\Program Files (x86)\Steam\steamapps\common\Outward\Outward_Defed\Outward Definitive Edition.exe"

REM ── Step 4: Wait for game WebSocket to come up, then start agent
echo [4/5] Starting agent (will retry until game is ready)...
timeout /t 5 /nobreak >nul
start "Voyager Agent" cmd /k "cd /d "C:\Projects\Outward Voyager\agent" && py main.py"

REM ── Step 5: Start dashboard
echo [5/5] Starting dashboard...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7770" ^| findstr "LISTENING"') do taskkill /f /pid %%a 2>nul
timeout /t 1 /nobreak >nul
start "Voyager Dashboard" cmd /k "cd /d "C:\Projects\Outward Voyager\dashboard" && py -m uvicorn server:app --port 7770"
timeout /t 2 /nobreak >nul
start "" "http://localhost:7770"

echo.
echo ====================================================
echo  All systems launching. Game takes ~30s to load.
echo  Agent will connect automatically once game is ready.
echo ====================================================
