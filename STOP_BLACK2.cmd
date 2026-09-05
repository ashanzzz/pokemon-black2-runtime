@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem STOP_BLACK2 stops every backend process owned by this checkout.
rem It deliberately does NOT close EmuHawk.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "tools\black2_launcher.py" stop-backend
) else (
  python "tools\black2_launcher.py" stop-backend
)
if errorlevel 1 pause
