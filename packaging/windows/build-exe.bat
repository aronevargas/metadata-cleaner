@echo off
rem Builds dist\metadata-editor\metadata-editor.exe (run on Windows). Extra args go to build-exe.ps1, e.g. -FfmpegDir C:\ffmpeg\bin -Zip
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-exe.ps1" %*
echo.
pause
