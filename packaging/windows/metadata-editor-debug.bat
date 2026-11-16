@echo off
rem Metadata Editor with a console: shows Python errors/tracebacks (troubleshooting).
cd /d "%~dp0..\.."
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" -X utf8 -m app %*) else (py -3 -X utf8 -m app %*)
echo.
echo Exit code: %errorlevel%
pause
