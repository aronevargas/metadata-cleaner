@echo off
rem Metadata Editor - opens the desktop window (no console). First time: packaging\windows\install.bat
rem Errors are shown in a message box; for full logs use packaging\windows\metadata-editor-debug.bat
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m app %*
  exit /b 0
)
echo Metadata Editor is not installed yet in this folder (.venv missing).
echo Run packaging\windows\install.bat first (needs Python 3.10+ from python.org).
where pyw >nul 2>nul
if %errorlevel%==0 (
  echo.
  echo Trying the system Python via the "pyw" launcher anyway...
  start "" pyw -3 -m app %*
)
echo.
pause
