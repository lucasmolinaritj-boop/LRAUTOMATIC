@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==============================================
echo  LRAutomatic - Instalacao
echo ==============================================
echo.

set "PYTHON_CMD="

rem Prefere Python 3.12, depois 3.11, depois qualquer Python 3 compativel.
py -3.12 -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3.12"

if not defined PYTHON_CMD (
  py -3.11 -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.11"
)

if not defined PYTHON_CMD (
  py -3 -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
  python -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo [ERRO] Python 3.11 ou superior nao foi encontrado.
  echo Instale Python 3.12 e marque a opcao Add Python to PATH.
  pause
  exit /b 1
)

echo [OK] Usando: %PYTHON_CMD%
%PYTHON_CMD% --version

rem Remove somente ambiente virtual incompleto ou corrompido.
if exist ".venv" if not exist ".venv\Scripts\python.exe" (
  echo [AVISO] Ambiente virtual incompleto encontrado. Recriando...
  rmdir /s /q ".venv"
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Criando ambiente virtual...
  %PYTHON_CMD% -m venv ".venv"
  if errorlevel 1 (
    echo [ERRO] Nao foi possivel criar o ambiente virtual.
    pause
    exit /b 1
  )
) else (
  echo [1/3] Ambiente virtual existente encontrado.
)

echo [2/3] Atualizando pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo [ERRO] Falha ao atualizar o pip.
  pause
  exit /b 1
)

echo [3/3] Instalando LRAutomatic...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
  echo [ERRO] Falha ao instalar as dependencias do projeto.
  pause
  exit /b 1
)

if not exist "config.json" copy /Y "config.example.json" "config.json" >nul

echo.
echo ==============================================
echo  INSTALACAO CONCLUIDA COM SUCESSO
echo ==============================================
echo.
echo Agora execute: abrir_app.bat
echo.
pause
