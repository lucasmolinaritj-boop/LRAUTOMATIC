@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado. Instale Python 3.11 ou superior.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 py -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -e .

if not exist config.json copy /Y config.example.json config.json >nul

echo.
echo Instalacao concluida.
echo Edite config.json e troque a api_key antes de iniciar.
echo Para iniciar: iniciar_servidor.bat
pause
