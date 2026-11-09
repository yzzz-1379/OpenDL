@echo off
setlocal
rem Launch without a console window. The launcher resolves its own package path
rem and shows a dialog if anything fails, so a silent exit is impossible.
where pythonw.exe >nul 2>nul
if errorlevel 1 goto fallback
start "" pythonw.exe "%~dp0launch.py"
exit /b 0

:fallback
echo pythonw.exe not found on PATH, using python.exe instead.
python "%~dp0launch.py"
if errorlevel 1 pause
