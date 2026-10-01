@echo off
echo ========================================================
echo Starting Kenya Public Finance Intelligence Platform...
echo ========================================================

echo Starting Python FastAPI Backend...
start cmd /k ".\venv\Scripts\activate && python api/server.py"

echo Starting Vite Frontend...
cd website
start cmd /k "npm run dev"

echo.
echo Both services are starting up!
echo Backend API will be available at: http://localhost:8000
echo Frontend will be available at: http://localhost:5173
echo.
echo To close, simply close the new command prompt windows.
pause
