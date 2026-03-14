@echo off
echo ====================================================
echo  Outward Voyager Dashboard
echo ====================================================
echo.
echo Opening http://localhost:8080 in 3 seconds...
echo.

cd /d "C:\Projects\Outward Voyager\dashboard"
start "" "http://localhost:8080"
uvicorn server:app --port 8080 --reload
