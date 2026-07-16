@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente virtual nao encontrado. Execute instalar.bat primeiro.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
start "LRAutomatic" lrautomatic-desktop
