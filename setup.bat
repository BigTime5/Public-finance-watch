@echo off
REM ══════════════════════════════════════════════════════
REM  Kenya Public Finance Intelligence — Windows Setup
REM ══════════════════════════════════════════════════════

echo.
echo  Kenya Public Finance Intelligence Platform
echo  ==========================================
echo.

REM Check Python
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo ERROR: Python not found. Please install Python 3.10+ or activate your conda env.
    echo   conda activate fresh-env
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
pip install -r requirements.txt
IF ERRORLEVEL 1 (
    echo ERROR: pip install failed. Try:
    echo   pip install -r requirements.txt --user
    pause
    exit /b 1
)

echo.
echo [2/3] Initialising database...
python main.py --init-only

echo.
echo [3/3] Ready! Choose a run mode:
echo.
echo   QUICK  (index only, no downloads, ~2 min):
echo     python main.py --sources ppra knbs oag --no-download --no-anomalies
echo.
echo   STANDARD  (2024+2025 PPRA, download files, ~10 min):
echo     python main.py --ppra-years 2024 2025 --export
echo.
echo   FULL  (all years 2018-2026, ~45-60 min):
echo     python main.py --all-years --export
echo.
echo   ANALYSIS  (after scraping, generate Excel report):
echo     python analyze.py
echo.
pause
