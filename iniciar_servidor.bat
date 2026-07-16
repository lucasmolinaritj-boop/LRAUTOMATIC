@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\lrautomatic.exe" (
  echo Execute instalar.bat primeiro.
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
lrautomatic serve
pause
