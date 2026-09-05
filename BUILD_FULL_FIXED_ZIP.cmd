@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "OUT=%~dp0pokemon-black2-runtime-v10.1-fixed-full-7bd1de2.zip"
echo [Black2] Building exact-base full source ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0MATERIALIZE_FULL_ZIP.ps1" -OutputZip "%OUT%"
if errorlevel 1 (
  echo.
  echo Build failed. Check that Git and PowerShell can access GitHub.
  pause
  exit /b 1
)
echo.
echo Complete: "%OUT%"
pause
