@echo off
chcp 65001 >nul
title Pokemon Black 2 Runtime Restart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\restart_runtime.ps1"
echo.
if errorlevel 1 (
  echo Restart failed. Keep this window open and share the error shown above.
) else (
  echo Restart completed. You can close this window.
)
pause

