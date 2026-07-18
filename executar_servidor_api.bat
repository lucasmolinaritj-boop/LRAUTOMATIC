@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHONW=%CD%\.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHONW%" exit /b 2
if not exist "%CD%\config.json" exit /b 3

"%PYTHONW%" -m lrautomatic.cli serve --config "%CD%\config.json" >> "%TEMP%\lrautomatic-api-launcher.log" 2>&1
exit /b %ERRORLEVEL%
