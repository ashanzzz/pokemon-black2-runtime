@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "tools\black2_launcher.py" gui
) else (
  start "" pythonw "tools\black2_launcher.py" gui
)
