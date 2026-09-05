@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem black2_launcher.py owns the per-checkout single-instance mutex.
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "tools\black2_launcher.py" gui
) else (
  start "" pythonw "tools\black2_launcher.py" gui
)
