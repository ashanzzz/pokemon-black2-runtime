@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Gracefully closes only the EmuHawk instance launched by this checkout.
rem It deliberately does NOT stop the backend.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "tools\black2_launcher.py" close-emulator
) else (
  python "tools\black2_launcher.py" close-emulator
)
if errorlevel 1 pause
