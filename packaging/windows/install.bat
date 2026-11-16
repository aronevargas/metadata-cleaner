@echo off
rem Installs Metadata Editor (.venv + dependencies + Start Menu/Desktop shortcuts).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Shortcut %*
echo.
pause
