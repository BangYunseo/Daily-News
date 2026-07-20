@echo off
REM Local launcher for the daily news brief.
REM Double-click this file to run main.py without typing any command.
REM API keys are loaded automatically from the .env file in this folder.

chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Daily-News : local brief run
echo ============================================
echo.

python main.py

echo.
echo ============================================
echo   Finished. Check the result above.
echo ============================================
pause
