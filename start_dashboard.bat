@echo off
echo ====================================================
echo  Outward Voyager Dashboard
echo ====================================================
echo.

echo Killing any existing process on port 7770...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7770" ^| findstr "LISTENING"') do taskkill /f /pid %%a 2>nul
timeout /t 1 /nobreak >nul

cd /d "C:\Projects\Outward Voyager\dashboard"

echo Starting Cloudflare tunnel (public URL will appear in a moment)...
start "Voyager Tunnel" py start_tunnel.py

echo Waiting for tunnel to connect...
timeout /t 8 /nobreak >nul

echo Opening http://localhost:7770 ...
start "" "http://localhost:7770"

py -m uvicorn server:app --port 7770
