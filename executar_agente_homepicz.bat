@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHONW_EXE=%CD%\.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW_EXE%" set "PYTHONW_EXE=%CD%\.venv\Scripts\python.exe"

if not exist "%PYTHONW_EXE%" (
  echo [%DATE% %TIME%] Python da venv nao encontrado.>> "%TEMP%\lrautomatic-agent-launcher.log"
  exit /b 1
)

if not exist "%CD%\config.json" (
  echo [%DATE% %TIME%] config.json nao encontrado.>> "%TEMP%\lrautomatic-agent-launcher.log"
  exit /b 1
)

start "LRAutomatic Session Agent" /b "%PYTHONW_EXE%" -m lrautomatic.session_agent "%CD%\config.json"
exit /b 0
